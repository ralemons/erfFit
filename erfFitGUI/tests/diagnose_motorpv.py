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
# Run it at UED, where EPICS is reachable. It reads only: no motor moves.
#
#   python tests/diagnose_motorpv.py UED:UM1:MMS:02

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

	ok = True
	for pv in sys.argv[1:]:
		if not diagnose(erfFit, caget, pv):
			ok = False

	return 0 if ok else 1


def diagnose(erfFit, caget, pv):
	banner('{}'.format(pv))

	# Which class the PV resolved to. This is decided purely by the PV
	# string: _GetMotorClass looks for :MMS:, :MMN: and friends in it, so a
	# motor whose name does not match its hardware gets the wrong class and
	# then asks the record for fields it does not have.
	try:
		cls = erfFit.motorClass(pv)
	except Exception as e:
		print('motorClass failed: {}: {}'.format(type(e).__name__, e))
		return False

	print('resolved class : {}'.format(cls.__name__))
	print('               (chosen from the PV string, not from the hardware)')

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
