#!/cds/sw/ds/ana/conda2/inst/envs/ps-4.6.3/bin/python
# Randy Lemons

# Why does a MotorRow say "disconnected"?
#
# MotorRow.refresh calls MotorRow.isDeviceConnected, which is ophyd's
# Device.connected, and that is True only when BOTH of these hold:
#
#   1. every non-lazy EPICS signal on the device class has connected, and
#   2. every _required_for_connection subscription has fired at least once.
#
# A motor whose .RBV reads perfectly well in caget still reports
# disconnected if one obscure field is missing from the record, or if the
# IOC never sends the initial monitor for .RBV or .DMOV. The GUI cannot
# tell those apart - it only has the one boolean - so this script asks the
# device directly and names the channels that are to blame.
#
# It also reports what the resolved motor class IS, which matters now that
# there are two of them. A Newport and a TwinCATMREAxis have no common base
# below FltMvInterface and carry .DMOV under different names, and pcdsdevices is
# not installed on S3DF, so the suites there can only check that against a
# stand-in they wrote themselves. This script is the only place the REAL class
# gets inspected, so run it once per motor type after any change to
# erfFit.motorClass or erfFit.isMoving.
#
# Run it at UED, where EPICS is reachable. It reads only: no motor moves.
#
#   python tests/diagnose_motorpv.py UED:UM1:MMS:02      a TwinCAT axis
#   python tests/diagnose_motorpv.py UED:USR:MMN:04:03   a Newport
#
# Both at once is the useful call, since the interesting failures are the ones
# where the two types behave differently.

import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import erfFitPaths

# How long to let channel access settle before judging. Generous, because
# the point of the script is to distinguish "slow" from "never".
SETTLE = 8.0


def banner(text):
	print('')
	print(text)
	print('-' * len(text))


def main():
	if len(sys.argv) < 2:
		print('usage: diagnose_motorpv.py <motor PV> [<motor PV> ...]')
		return 2

	for extra in erfFitPaths.moduleSearchDirs():
		if extra not in sys.path:
			sys.path.append(extra)

	try:
		import erfFit
	except Exception as e:
		print('could not import erfFit: {}: {}'.format(type(e).__name__, e))
		print('Without it there is no motor class to test. This is the same')
		print('failure the GUI reports as "cannot connect".')
		return 1

	from epics import caget

	reportModules()

	ok = True
	for pv in sys.argv[1:]:
		if not diagnose(erfFit, caget, pv):
			ok = False

	return 0 if ok else 1


# What MotorRow and erfFit.runMotor read off a device. The stub based suites on
# S3DF can only check these against a stand-in, which proves the test agrees
# with itself and nothing more. Here the class is the real one.
NEEDED = ('mv', 'mvr', 'position', 'egu', 'stop', 'check_value', 'moving')


def reportClass(erfFit, cls):
	# The lineage, because erfFit.isMoving dispatches on it. A Newport is an
	# EpicsMotor and calls .DMOV motor_done_move; a TwinCAT axis is a
	# PVPositioner driving the same record and calls it done. The two branches
	# are disjoint, and if that ever stopped being true the first branch would
	# swallow both types and read a component the other does not have - which
	# isMoving catches and turns into None, so nothing would complain while
	# every move quietly fell back to watching the readback.
	banner('Class lineage and surface')

	bases = ' <- '.join([k.__name__ for k in cls.__mro__[:6]])
	print('  mro            : {}'.format(bases))

	isEpics = issubclass(cls, erfFit.EpicsMotorInterface)
	isTwinCAT = issubclass(cls, erfFit.TwinCATMotorInterface)
	print('  EpicsMotorInterface  : {}'.format(isEpics))
	print('  TwinCATMotorInterface: {}'.format(isTwinCAT))

	if isEpics and isTwinCAT:
		print('  *** both at once. isMoving would take the EpicsMotorInterface')
		print('      branch for every motor. This is a real problem.')
	elif isEpics:
		print('  isMoving reads     : motor_done_move  (.DMOV)')
	elif isTwinCAT:
		print('  isMoving reads     : done             (.DMOV)')
	else:
		print('  *** neither. isMoving will return None for this motor and')
		print('      every move will be judged by whether the readback is')
		print('      still changing, which is the weaker check.')

	# Which of the two .DMOV names the class actually has. Asserting the
	# dispatch is right is not the same as the component being there.
	print('  has motor_done_move  : {}'.format(hasattr(cls, 'motor_done_move')))
	print('  has done             : {}'.format(hasattr(cls, 'done')))

	missing = [a for a in NEEDED if not hasattr(cls, a)]
	if missing:
		print('  *** missing what the GUI reads: {}'.format(', '.join(missing)))
		print('      MotorRow would raise on these, mostly when somebody jogs.')
	else:
		print('  has everything the GUI reads: {}'.format(', '.join(NEEDED)))

	# retry_deadband is absent on both supported classes, so the 10 um / 2 um
	# fallback is what decides arrival. Reported rather than assumed, because a
	# class that did map .RDBD would change that silently.
	print('  has retry_deadband   : {} (fallback tolerance is used if False)'.format(
		hasattr(cls, 'retry_deadband')))


def reportModules():
	# The version wall. TwinCATMREAxis first appeared in pcdsdevices v10.4.0,
	# and erfFit appends a hardcoded site-packages path, so it is worth saying
	# out loud which pcdsdevices actually got imported.
	banner('pcdsdevices')
	try:
		import pcdsdevices
		print('  version : {}'.format(getattr(pcdsdevices, '__version__', 'unknown')))
		print('  path    : {}'.format(getattr(pcdsdevices, '__file__', 'unknown')))
	except Exception as e:
		print('  import failed: {}: {}'.format(type(e).__name__, e))
		return

	for module, name in (('pcdsdevices.epics_motor', 'Newport'),
			('pcdsdevices.twincat_motor', 'TwinCATMREAxis')):
		try:
			loaded = __import__(module, fromlist=[name])
			print('  {:<30} {}'.format(module, 'has ' + name
				if hasattr(loaded, name) else '*** no ' + name))
		except Exception as e:
			print('  {:<30} *** {}: {}'.format(module, type(e).__name__, e))


def diagnose(erfFit, caget, pv):
	banner('{}'.format(pv))

	# Which class the PV resolved to. This is decided purely by the PV string:
	# erfFit.motorClass looks for MMN and MMS in it, so a motor whose name does
	# not match its hardware gets the wrong class and then asks the record for
	# fields it does not have.
	#
	# erfFit owns that two entry table rather than calling
	# pcdsdevices._GetMotorClass, and this is the script that proves it has to.
	# _GetMotorClass maps MMS to IMS, which is right everywhere at PCDS except
	# here: UED's MMS motors are TwinCAT axes on a tcmotor record, and IMS wants
	# .RINI, .PN and :SEQ_SELN, which such an IOC does not serve. Run this
	# against an MMS PV and the missing channel list below is what that mistake
	# would have looked like.
	try:
		cls = erfFit.motorClass(pv)
	except Exception as e:
		print('motorClass failed: {}: {}'.format(type(e).__name__, e))
		return False

	print('resolved class : {}'.format(cls.__name__))
	print('               (chosen from the PV string, not from the hardware)')

	reportClass(erfFit, cls)

	try:
		device = erfFit.getMotor(pv, 'diagnostic', {})
	except Exception as e:
		print('getMotor failed: {}: {}'.format(type(e).__name__, e))
		return False

	print('waiting {:.0f}s for channel access...'.format(SETTLE))
	time.sleep(SETTLE)

	connected = False
	try:
		connected = bool(device.connected)
	except Exception as e:
		print('reading .connected raised: {}: {}'.format(type(e).__name__, e))

	print('device.connected : {}'.format(connected))

	# Part 1: the signals. Anything False here is a channel the record is
	# not serving, and every one of them holds .connected down on its own.
	dead = []
	live = 0
	for walk in device.walk_signals(include_lazy=False):
		signal = walk.item
		name = getattr(signal, 'pvname', walk.dotted_name)
		try:
			isUp = bool(signal.connected)
		except Exception:
			isUp = False
		if isUp:
			live += 1
		else:
			dead.append((walk.dotted_name, name))

	print('signals        : {} connected, {} not'.format(live, len(dead)))

	if dead:
		banner('Channels this motor class wants and the IOC is not serving')
		for dotted, name in dead:
			# caget directly, so a signal that ophyd has not connected but
			# that genuinely exists is not blamed. Short timeout: a missing
			# channel costs the whole of it.
			value = caget(name, timeout=1.0)
			verdict = 'missing' if value is None else 'exists, ophyd has not connected it'
			print('  {:<22} {:<34} {}'.format(dotted, name, verdict))

		print('')
		print('Every line above holds device.connected at False, which is')
		print('the only thing MotorRow gets to look at. A field that reads')
		print('"missing" means this record does not have it, so the class is')
		print('wrong for this motor rather than the motor being down.')

	# Part 2: the subscriptions. These are the subtle half. ophyd will not
	# call a device connected until _pos_changed and _move_changed have each
	# been delivered a value, and those ride on monitors from .RBV and
	# .DMOV. An IOC that answers a get but sends no monitor leaves the
	# device disconnected for ever with every signal showing connected -
	# which is exactly the shape of the old MoveStatus.done bug.
	pending = {}
	try:
		pending = dict(getattr(device, '_required_for_connection', {}) or {})
	except Exception:
		pass

	if pending:
		banner('Subscriptions that have not fired yet')
		for key, description in pending.items():
			print('  {}'.format(description))
		print('')
		print('These are initial monitor callbacks, not gets. If the signals')
		print('above are all connected but these remain pending, the IOC is')
		print('answering reads while sending no monitors.')

	# The readback is what the user actually wants and it is worth saying
	# plainly whether it is readable, because "disconnected" on the row is
	# compatible with a perfectly good position.
	banner('What can actually be read')
	rbv = caget(pv + '.RBV', timeout=1.0)
	print('  {}.RBV = {}'.format(pv, rbv))
	desc = caget(pv + '.DESC', timeout=1.0)
	print('  {}.DESC = {}'.format(pv, desc))
	dmov = caget(pv + '.DMOV', timeout=1.0)
	print('  {}.DMOV = {}'.format(pv, dmov))

	# The dispatch, exercised rather than reasoned about. This is the one line
	# that cannot be checked anywhere but here: True or False means isMoving
	# found the right component on the real class, and None means it did not and
	# every move on this motor will be judged by the readback instead.
	#
	# A motor sitting still should print False. None is the answer to worry
	# about, and if .DMOV above read 0 or 1 while this says None, the component
	# name is wrong for this class rather than the record being short of fields.
	moving = erfFit.isMoving(device)
	print('  erfFit.isMoving  = {}{}'.format(moving,
		'   *** no .DMOV found on this device' if moving is None else ''))

	try:
		print('  device.position  = {}'.format(device.position))
	except Exception as e:
		print('  device.position raised: {}: {}'.format(type(e).__name__, e))
	try:
		print('  device.egu       = {!r}'.format(device.egu))
	except Exception as e:
		print('  device.egu raised: {}: {}'.format(type(e).__name__, e))

	if rbv is not None and not connected:
		print('')
		print('The position reads but the row still says disconnected. The')
		print('motor is alive; the device class is asking for more than this')
		print('record provides, or no monitors are arriving.')

	if connected:
		print('')
		print('This motor is fully connected. If the row still says')
		print('disconnected, the row never had Connect motors pressed, or it')
		print('holds a different device object than this script built.')

	return connected


if __name__ == '__main__':
	sys.exit(main())
