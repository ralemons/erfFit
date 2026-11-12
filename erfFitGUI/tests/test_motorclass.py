# Motor class resolution from the PV name
# Randy Lemons

# The motor type is a field in the PV name, so the device class is deduced
# rather than configured: 'MMN' is a Newport and 'MMS' a TwinCAT axis. PCDS has
# said those two are the only types UED will see for the foreseeable future, so
# erfFit.motorClass now owns that table itself in two lines rather than asking
# pcdsdevices._GetMotorClass.
#
# That is a deliberate narrowing, and the reason is not just simplicity.
# _GetMotorClass maps 'MMS' to IMS, because at the rest of PCDS that is what an
# MMS is. UED's MMS motors are TwinCAT axes on a tcmotor record, and IMS adds
# .RINI, .PN and :SEQ_SELN, none of which such an IOC serves. Those components
# are not lazy, so Device.connected would never go True and the jog row would
# read 'disconnected' for ever while caget worked perfectly. Asking pcdsdevices
# would therefore give the WRONG class for exactly the motors this change is
# about, which is why the table moved here.
#
# The cost is that MMB, MCS, MMC, MZM, PIC and HEX are now refused, where the
# old guard accepted the first five. That is the intended policy and is asserted
# below so it cannot be lost by accident.
#
# pcdsdevices is not installed on S3DF, so neither real class can be imported.
# The stand-ins below mirror the real lineage, which is the one property the
# rest of erfFit leans on:
#
#   Newport         -> PCDSMotorBase -> EpicsMotorInterface(FltMvInterface, EpicsMotor)
#   TwinCATMREAxis  -> TwinCATAxis   -> TwinCATMotorInterface(FltMvInterface, PVPositioner)
#
# The common ancestor is FltMvInterface, NOT EpicsMotor, and the two branches
# are disjoint. erfFit.isMoving dispatches on exactly that, so the disjointness
# is asserted here rather than assumed.
#
# What a stub cannot prove is that the REAL TwinCATMREAxis carries the surface
# MotorRow reads - a stub only proves the test agrees with itself. That check
# needs live pcdsdevices and lives in diagnose_motorpv.py, which runs at UED.
#
# Nothing connects and nothing moves. The PVs below do not exist, and no device
# is constructed at all - only classes are resolved.

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ophyd import EpicsMotor, PVPositioner


# FltMvInterface is where mv/mvr come from, and it is the ONLY thing the two
# branches share. Declared first so both lineages below pick it up the way the
# real ones do.
class FltMvInterface:
	def mv(self, position, timeout = None, wait = False, log = True):
		pass

	def mvr(self, delta, timeout = None, wait = False, log = True):
		pass


# The Newport branch. EpicsMotorInterface is declared
# EpicsMotorInterface(FltMvInterface, EpicsMotor) in pcdsdevices, so it is the
# tightest base that still guarantees mv/mvr on a motor record device.
class EpicsMotorInterface(FltMvInterface, EpicsMotor):
	pass


class PCDSMotorBase(EpicsMotorInterface):
	pass


class Newport(PCDSMotorBase):
	pass


# The TwinCAT branch. A TwinCAT axis is a PVPositioner, not an EpicsMotor: it
# drives a tcmotor record through .VAL/.RBV/.DMOV but presents them under the
# PVPositioner names setpoint/readback/done. ophyd's PVPositioner already
# supplies position, egu, stop, check_value, moving, limits and done, so this
# stub inherits the same surface the real class does rather than faking it.
class TwinCATMotorInterface(FltMvInterface, PVPositioner):
	pass


class TwinCATAxis(TwinCATMotorInterface):
	pass


class TwinCATMREAxis(TwinCATAxis):
	pass


# Classes the old table mapped and erfFit no longer accepts. Kept so the
# narrowing can be asserted against something real rather than against a bare
# string.
class IMS(PCDSMotorBase):
	pass


class BeckhoffAxis(EpicsMotorInterface):
	pass


class SmarAct(EpicsMotorInterface):
	pass


fakePcds = types.ModuleType('pcdsdevices')

fakeMotor = types.ModuleType('pcdsdevices.epics_motor')
fakeMotor.Newport = Newport
fakeMotor.EpicsMotorInterface = EpicsMotorInterface
fakeMotor.IMS = IMS
fakeMotor.BeckhoffAxis = BeckhoffAxis
fakeMotor.SmarAct = SmarAct

fakeTwinCAT = types.ModuleType('pcdsdevices.twincat_motor')
fakeTwinCAT.TwinCATMotorInterface = TwinCATMotorInterface
fakeTwinCAT.TwinCATAxis = TwinCATAxis
fakeTwinCAT.TwinCATMREAxis = TwinCATMREAxis

fakePcds.epics_motor = fakeMotor
fakePcds.twincat_motor = fakeTwinCAT
sys.modules['pcdsdevices'] = fakePcds
sys.modules['pcdsdevices.epics_motor'] = fakeMotor
sys.modules['pcdsdevices.twincat_motor'] = fakeTwinCAT

fakeMpl = types.ModuleType('matplotlib')
fakePlt = types.ModuleType('matplotlib.pyplot')
for attr in ('figure', 'clf', 'plot', 'xlabel', 'ylabel', 'title', 'show'):
	setattr(fakePlt, attr, lambda *a, **k: None)
fakeMpl.pyplot = fakePlt
sys.modules.setdefault('matplotlib', fakeMpl)
sys.modules.setdefault('matplotlib.pyplot', fakePlt)

import erfFit

# The attributes MotorRow and runMotor read off a device. Checked on the class,
# so nothing is constructed and nothing connects.
NEEDED = ('mv', 'mvr', 'position', 'egu', 'stop', 'check_value', 'moving')

# Real UED motor PVs. The MMN ones are the existing Newports; UED:UM1:MMS:02 is
# the new chamber motor, and is the PV in solidState_KnifeEdge.txt.
NEWPORT_PVS = ('UED:USR:MMN:04:03', 'UED:USR:MMN:04:02', 'UED:USR:MMN:13:05')
TWINCAT_PVS = ('UED:UM1:MMS:02', 'UED:USR:MMS:01:01')

# Types the old _GetMotorClass table resolved and this version refuses.
RETIRED_PVS = (
	('UED:USR:MMB:01:01', 'Beckhoff axis'),
	('UED:USR:MCS:01:01', 'SmarAct'),
	('UED:USR:MMC:01:01', 'MMC100'),
	('UED:USR:MZM:01:01', 'PMC100'),
	('UED:USR:PIC:01:01', 'PCDSMotorBase'),
	('UED:USR:HEX:01:01', 'hexapod'),
	('UED:USR:CLZ:01:01', 'IMS by CLZ'),
)


def main():
	bad = 0

	def check(label, got, expect):
		nonlocal bad
		# A class is callable, and much of what this suite compares IS a class,
		# so only a plain function counts as a predicate here.
		if isinstance(expect, type):
			ok = got is expect
		elif callable(expect):
			ok = expect(got)
		else:
			ok = got == expect
		print('  {:<52} {:<30} {}'.format(label, repr(got), 'OK' if ok else 'BAD'))
		if not ok:
			bad += 1

	def checkRaises(label, call, kind):
		nonlocal bad
		try:
			call()
			got = 'did not raise'
			ok = False
		except kind as e:
			got = '{}: {}'.format(type(e).__name__, e)
			ok = True
		except Exception as e:
			got = '{}: {}'.format(type(e).__name__, e)
			ok = False
		print('  {:<52} {:<30} {}'.format(label, 'raised' if ok else repr(got),
			'OK' if ok else 'BAD'))
		if not ok:
			bad += 1
			print('      {}'.format(got))

	print('=== the existing UED motors keep the class they have today ===')
	# The regression that matters. Every UED motor PV was an MMN before this
	# change, so it must be a no-op for all of them: a Newport before, a
	# Newport after.
	for pv in NEWPORT_PVS:
		check(pv, erfFit.motorClass(pv), Newport)

	print('\n=== an MMS is a TwinCAT axis, not an IMS ===')
	# The whole point of owning the table. pcdsdevices would say IMS here.
	for pv in TWINCAT_PVS:
		check(pv, erfFit.motorClass(pv), TwinCATMREAxis)
	check('and NOT the IMS pcdsdevices would give',
		erfFit.motorClass('UED:UM1:MMS:02') is IMS, False)
	# MRE, not the PLC-tag TwinCATAxis: UED's MMS motors are on a tcmotor
	# record, so the motor-record edition is the right leaf.
	check('it is the motor record edition',
		erfFit.motorClass('UED:UM1:MMS:02') is TwinCATAxis, False)

	print('\n=== the two branches are disjoint, which isMoving relies on ===')
	# isMoving is an if/elif on these two bases. If either were a subclass of
	# the other the first branch would swallow both types and read the wrong
	# signal, so this is load bearing rather than trivia.
	check('a Newport is an EpicsMotorInterface',
		issubclass(Newport, EpicsMotorInterface), True)
	check('a TwinCAT axis is NOT',
		issubclass(TwinCATMREAxis, EpicsMotorInterface), False)
	check('a TwinCAT axis is a TwinCATMotorInterface',
		issubclass(TwinCATMREAxis, TwinCATMotorInterface), True)
	check('a Newport is NOT',
		issubclass(Newport, TwinCATMotorInterface), False)
	check('their only shared base is FltMvInterface',
		issubclass(Newport, FltMvInterface) and issubclass(TwinCATMREAxis, FltMvInterface),
		True)
	check('a TwinCAT axis is not an EpicsMotor at all',
		issubclass(TwinCATMREAxis, EpicsMotor), False)

	print('\n=== both supported classes can be driven ===')
	for kind in (Newport, TwinCATMREAxis):
		missing = [a for a in NEEDED if not hasattr(kind, a)]
		check('{} has what MotorRow reads'.format(kind.__name__), missing, [])
	# The names .DMOV hides behind. This is the difference isMoving exists for.
	check('a Newport calls .DMOV motor_done_move',
		hasattr(Newport, 'motor_done_move'), True)
	check('a TwinCAT axis calls it done', hasattr(TwinCATMREAxis, 'done'), True)
	check('and has no motor_done_move',
		hasattr(TwinCATMREAxis, 'motor_done_move'), False)

	print('\n=== every other type is refused, on purpose ===')
	# These are not oversights. PCDS has said UED will only have Newport and
	# TwinCAT motors, so anything else is a config mistake worth a message
	# rather than a half connected device. MMB, MCS, MMC, MZM and PIC used to
	# be accepted by the old EpicsMotorInterface guard.
	for pv, what in RETIRED_PVS:
		checkRaises('{} ({}) refused'.format(pv, what),
			lambda pv = pv: erfFit.motorClass(pv), TypeError)

	# Both of these are real UED PVs, from ued/beamline.py: the laser delay
	# stages carry no type field, so a typeless PV is reachable in practice and
	# not only by a typo.
	checkRaises('MOTR:AS01:MC06:CH5:MOTOR is refused',
		lambda: erfFit.motorClass('MOTR:AS01:MC06:CH5:MOTOR'), TypeError)
	checkRaises('MOTR:AS01:MC01:CH3:MOTOR is refused',
		lambda: erfFit.motorClass('MOTR:AS01:MC01:CH3:MOTOR'), TypeError)
	checkRaises('a PV with no type field is refused',
		lambda: erfFit.motorClass('UED:USR:XYZ:04:03'), TypeError)
	checkRaises('an empty PV is refused',
		lambda: erfFit.motorClass(''), TypeError)
	checkRaises('getMotor refuses it too, before connecting',
		lambda: erfFit.getMotor('UED:USR:XYZ:04:03', 'nope', {}), TypeError)

	print('\n=== a PV naming both types is refused rather than guessed ===')
	# PCDS numbering accidents are not erfFit's to resolve, and picking one of
	# the two silently would drive a motor through the wrong record layout.
	checkRaises('MMS and MMN together refused',
		lambda: erfFit.motorClass('UED:USR:MMS:MMN:01'), TypeError)
	checkRaises('the other order too',
		lambda: erfFit.motorClass('UED:USR:MMN:MMS:01'), TypeError)
	try:
		erfFit.motorClass('UED:USR:MMS:MMN:01')
		both = ''
	except TypeError as e:
		both = str(e)
	check('the message names both types',
		'MMS' in both and 'MMN' in both, True)

	print('\n=== the error message is useful ===')
	try:
		erfFit.motorClass('UED:USR:XYZ:04:03')
		text = ''
	except TypeError as e:
		text = str(e)
	# Only the PV is asserted. The wording beyond that is free to change, and a
	# test that pins the prose just breaks every time somebody rephrases it.
	check('names the PV', 'UED:USR:XYZ:04:03' in text, True)
	check('is not empty', len(text) > 20, True)

	print('\n=== refusal happens before any device is built ===')
	# The guard is a string test, so a bad PV never reaches a constructor and
	# never leaves a half connected device in the master dict.
	shared = {}
	try:
		erfFit.getMotor('UED:USR:XYZ:04:03', 'nope', shared)
	except TypeError:
		pass
	check('nothing was cached', len(shared), 0)

	print('\n=== sharing still works, and the class comes from the PV ===')
	check('motorClass is exported', hasattr(erfFit, 'motorClass'), True)
	check('getMotor is still exported', hasattr(erfFit, 'getMotor'), True)
	# The class is a function of the PV alone, so the cache cannot disagree
	# with itself: one PV, one class, every time.
	check('the same PV resolves the same class twice',
		erfFit.motorClass('UED:USR:MMN:04:03') is erfFit.motorClass('UED:USR:MMN:04:03'),
		True)
	check('different types for different PVs',
		erfFit.motorClass('UED:USR:MMN:04:03') is not erfFit.motorClass('UED:UM1:MMS:02'),
		True)

	print('\n=== scan() accepts either supported motor ===')
	import inspect
	source = inspect.getsource(erfFit.scan.__init__)
	# The check is a tuple of the two base classes now. Both names have to be
	# in it, or one motor type silently cannot be put in a motorList.
	check('it checks EpicsMotorInterface', 'EpicsMotorInterface' in source, True)
	check('it checks TwinCATMotorInterface', 'TwinCATMotorInterface' in source, True)
	check('erfFit imported the Newport base',
		hasattr(erfFit, 'EpicsMotorInterface'), True)
	check('erfFit imported the TwinCAT base',
		hasattr(erfFit, 'TwinCATMotorInterface'), True)
	# _GetMotorClass is no longer consulted. Importing it again would quietly
	# reintroduce the MMS -> IMS mapping this module exists to avoid.
	check('_GetMotorClass is not imported any more',
		hasattr(erfFit, '_GetMotorClass'), False)

	print('\nFAILURES: {}'.format(bad))
	return bad


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
