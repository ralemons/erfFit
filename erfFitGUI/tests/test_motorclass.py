# Motor class resolution from the PV name
# Randy Lemons

# The motor type is a field in the PV name, so the device class is deduced
# rather than configured: ':MMN:' is a Newport, ':MMB:' a Beckhoff axis,
# ':MCS:' a SmarAct. pcdsdevices owns that table and erfFit.motorClass asks it,
# adding one guard - a PV matching nothing resolves to a bare ophyd EpicsMotor,
# which has no mv() or mvr(), and must be refused rather than handed to a jog
# button.
#
# pcdsdevices is not installed on S3DF, so the table itself cannot be imported.
# It is pure string matching with no channel access, so it is reimplemented
# here from the real source and the two are checked to agree in shape: what
# this suite asserts is that erfFit does the right thing WITH the table, and
# that the fallback is rejected. The real table is at
# pcdsdevices/epics_motor.py, in _GetMotorClass.
#
# Nothing connects and nothing moves. The PVs below do not exist, and no device
# is constructed at all - only classes are resolved.

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ophyd import EpicsMotor


# Stand-ins mirroring the real lineage. EpicsMotorInterface is declared
# EpicsMotorInterface(FltMvInterface, EpicsMotor) in pcdsdevices, so it is the
# tightest base that still guarantees mv/mvr. The leaf classes below are what
# the PV table maps to.
class FltMvInterface:
	def mv(self, position, timeout = None, wait = False, log = True):
		pass

	def mvr(self, delta, timeout = None, wait = False, log = True):
		pass


class EpicsMotorInterface(FltMvInterface, EpicsMotor):
	pass


class PCDSMotorBase(EpicsMotorInterface):
	pass


class IMS(PCDSMotorBase):
	pass


class Newport(PCDSMotorBase):
	pass


class PMC100(PCDSMotorBase):
	pass


class MMC100(PCDSMotorBase):
	pass


class BeckhoffAxis(EpicsMotorInterface):
	pass


class SmarAct(EpicsMotorInterface):
	pass


# PI_M824 is a PVPositioner, not an EpicsMotor, so it sits outside the branch
# the guard admits. Hexapods are deliberately not supported.
class PI_M824:
	pass


# Transcribed from pcdsdevices _GetMotorClass, including the fallback and the
# ':KEY:' match requiring a colon on both sides.
MOTOR_TYPES = (
	('MMS', IMS),
	('CLZ', IMS),
	('CLF', IMS),
	('MMN', Newport),
	('MZM', PMC100),
	('MMC', MMC100),
	('MMB', BeckhoffAxis),
	('PIC', PCDSMotorBase),
	('MCS', SmarAct),
	('MCS2', SmarAct),
	('HEX', PI_M824),
)


def stubGetMotorClass(basepv):
	for abbrev, kind in MOTOR_TYPES:
		if ':{}:'.format(abbrev) in basepv:
			return kind
	return EpicsMotor


fakePcds = types.ModuleType('pcdsdevices')
fakeMotor = types.ModuleType('pcdsdevices.epics_motor')
fakeMotor.Newport = Newport
fakeMotor.EpicsMotorInterface = EpicsMotorInterface
fakeMotor._GetMotorClass = stubGetMotorClass
fakePcds.epics_motor = fakeMotor
sys.modules['pcdsdevices'] = fakePcds
sys.modules['pcdsdevices.epics_motor'] = fakeMotor

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


def main():
	bad = 0

	def check(label, got, expect):
		nonlocal bad
		# A class is callable, and most of what this suite compares IS a class,
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

	print('=== the UED motors keep the class they have today ===')
	# The regression that matters. Every UED motor PV is an MMN, so this change
	# must be a no-op there: a Newport before, a Newport after.
	for pv in ('UED:USR:MMN:04:03', 'UED:USR:MMN:04:02', 'UED:USR:MMN:13:05'):
		check(pv, erfFit.motorClass(pv), Newport)

	print('\n=== the other types in the table resolve ===')
	check('UED:USR:MMB:01:01 is a Beckhoff axis',
		erfFit.motorClass('UED:USR:MMB:01:01'), BeckhoffAxis)
	check('UED:USR:MCS:01:01 is a SmarAct',
		erfFit.motorClass('UED:USR:MCS:01:01'), SmarAct)
	check('UED:USR:MMS:01:01 is an IMS',
		erfFit.motorClass('UED:USR:MMS:01:01'), IMS)
	check('UED:USR:MMC:01:01 is an MMC100',
		erfFit.motorClass('UED:USR:MMC:01:01'), MMC100)
	check('UED:USR:MZM:01:01 is a PMC100',
		erfFit.motorClass('UED:USR:MZM:01:01'), PMC100)
	check('UED:USR:PIC:01:01 is a PCDSMotorBase',
		erfFit.motorClass('UED:USR:PIC:01:01'), PCDSMotorBase)

	print('\n=== every supported class can be driven ===')
	for kind in (IMS, Newport, PMC100, MMC100, BeckhoffAxis, SmarAct, PCDSMotorBase):
		missing = [a for a in NEEDED if not hasattr(kind, a)]
		check('{} has what MotorRow reads'.format(kind.__name__), missing, [])
	check('all of them are EpicsMotorInterface',
		all(issubclass(k, EpicsMotorInterface) for k in
			(IMS, Newport, PMC100, MMC100, BeckhoffAxis, SmarAct, PCDSMotorBase)), True)

	print('\n=== the bare ophyd fallback is refused ===')
	# A PV naming no type resolves to ophyd's EpicsMotor, which has no mv/mvr.
	# Left alone it would raise AttributeError inside MotorRow.startMove, i.e.
	# when somebody presses jog, so motorClass has to refuse it first.
	check('the fallback really is ophyd EpicsMotor',
		stubGetMotorClass('MOTR:AS01:MC06:CH5:MOTOR'), EpicsMotor)
	check('and really has no mv', hasattr(EpicsMotor, 'mv'), False)
	check('and really has no mvr', hasattr(EpicsMotor, 'mvr'), False)
	# This is why the guard is not written against ophyd's EpicsMotor: the
	# fallback IS that class, and a class is its own subclass, so such a test
	# would accept exactly the case being rejected here.
	check('issubclass(EpicsMotor, EpicsMotor) is True',
		issubclass(EpicsMotor, EpicsMotor), True)
	check('but it is not an EpicsMotorInterface',
		issubclass(EpicsMotor, EpicsMotorInterface), False)

	# Both of these are real UED PVs, from ued/beamline.py: the laser delay
	# stages carry no type field, so the fallback is reachable in practice and
	# not only by a typo.
	checkRaises('MOTR:AS01:MC06:CH5:MOTOR is refused',
		lambda: erfFit.motorClass('MOTR:AS01:MC06:CH5:MOTOR'), TypeError)
	checkRaises('MOTR:AS01:MC01:CH3:MOTOR is refused',
		lambda: erfFit.motorClass('MOTR:AS01:MC01:CH3:MOTOR'), TypeError)
	checkRaises('a PV with no type field is refused',
		lambda: erfFit.motorClass('UED:USR:XYZ:04:03'), TypeError)
	# ':MMN:' needs a colon on both sides, so a truncated PV does NOT match.
	checkRaises('a truncated MMN PV is refused',
		lambda: erfFit.motorClass('UED:USR:MMN'), TypeError)
	checkRaises('getMotor refuses it too, before connecting',
		lambda: erfFit.getMotor('UED:USR:XYZ:04:03', 'nope', {}), TypeError)

	print('\n=== a hexapod is out of scope, and says so ===')
	check('HEX resolves to PI_M824 in the table',
		stubGetMotorClass('UED:USR:HEX:01:01'), PI_M824)
	checkRaises('but erfFit refuses it',
		lambda: erfFit.motorClass('UED:USR:HEX:01:01'), TypeError)

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
		erfFit.motorClass('UED:USR:MMN:04:03') is not erfFit.motorClass('UED:USR:MMB:01:01'),
		True)

	print('\n=== scan() accepts any supported motor, not just Newport ===')
	import inspect
	source = inspect.getsource(erfFit.scan.__init__)
	check('the Newport only type check is gone', 'Newport' in source, False)
	check('it checks EpicsMotorInterface instead',
		'EpicsMotorInterface' in source, True)

	print('\nFAILURES: {}'.format(bad))
	return bad


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
