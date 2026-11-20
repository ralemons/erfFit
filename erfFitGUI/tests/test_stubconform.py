#!/reg/g/pcds/pyps/pixi/ctrlenv-bluesky/latest-released/src/rhel9-x86_64/.pixi/envs/default/bin/python
# Randy Lemons

# Do the stubs in tests/ still describe real pcdsdevices?
#
# The other motor suites run with no pcdsdevices at all, which is what makes
# them fast and what lets them run under any python on S3DF. The price is that
# every fact about the real motor classes is written down twice: once in a stub
# and once in the library, and only one of the two copies gets upgraded. This
# suite is the diff between them, so stub drift fails here instead of at UED.
#
# It needs real pcdsdevices, so run it under the replica env on S3DF
#
#   PYTHONDONTWRITEBYTECODE=1 \
#     /sdf/home/r/rlemons/erfFitTestEnv/env/bin/python tests/test_stubconform.py
#
# or under the pixi env at UED. Anywhere else pcdsdevices will not import and
# this SKIPS with status 0, so it can sit in the same list as the fast suites
# without turning the psana python red. A skip is not a pass: if it never runs
# anywhere, the stubs are unchecked again.
#
# It reads classes, never records. Nothing connects and nothing moves, and the
# attributes are read off the CLASS rather than an instance on purpose - on
# pcdsdevices 11.1.1 interface.py wraps __getattribute__ with
# raise_if_disconnected, so hasattr() on a disconnected DEVICE raises, while
# the class itself answers freely.
#
# This does not replace diagnose_motorpv.py. Everything here is the class
# layout, which is knowable off hardware; what only that script can do is bind
# a real class to a live record and report which channel failed to connect.
#
# Import order is load bearing. Every stub module assigns a fake pcdsdevices
# into sys.modules as a side effect of being imported, so the real library and
# erfFit have to be imported and captured BEFORE the first stub module is
# touched. Nothing below the "the stubs themselves" banner may move above it.

import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
	from pcdsdevices.epics_motor import EpicsMotorInterface, PCDSMotorBase, Newport
	from pcdsdevices.twincat_motor import (TwinCATMotorInterface, TwinCATAxis,
		TwinCATMREAxis)
except ImportError as e:
	print('SKIP: real pcdsdevices did not import ({})'.format(e))
	print('      Run this under the replica env on S3DF or the pixi env at UED.')
	sys.exit(0)

import pcdsdevices

# Bound while the REAL library is still the one in sys.modules. Every stub
# module below is about to replace it.
import erfFit


# The names the production code actually says out loud on a device. erfFit.py
# and erfFit_GUI.py are the authority for this list, not pcdsdevices: a name
# that nothing calls does not need a stub and does not need checking.
SHARED_SURFACE = ('mv', 'mvr', 'stop', 'position', 'egu', 'moving', 'connected',
	'check_value', 'limits', 'low_limit', 'high_limit')

# Which signal carries .DMOV, per class. The whole reason erfFit.isMoving
# dispatches on type rather than just reading one attribute.
DONE_SIGNAL = {'Newport': 'motor_done_move', 'TwinCATMREAxis': 'done'}


def main():

	bad = 0

	def check(label, got, expect):
		nonlocal bad
		# A class is callable and several of the things compared here ARE
		# classes, so only a plain function counts as a predicate.
		if isinstance(expect, type):
			ok = got is expect
		elif callable(expect):
			ok = expect(got)
		else:
			ok = got == expect
		print('  {:<54} {:<28} {}'.format(label, repr(got), 'OK' if ok else 'BAD'))
		if not ok:
			bad += 1

	print('real pcdsdevices {} under python {}.{}.{}'.format(
		getattr(pcdsdevices, '__version__', '?'), *sys.version_info[:3]))

	print('\n=== the isinstance dispatch is unambiguous ===')
	# erfFit.isMoving tests EpicsMotorInterface first and TwinCATMotorInterface
	# second. If a class ever satisfied both, the first branch would win
	# silently and a TwinCAT axis would be read on a signal it does not have.
	check('a Newport is an EpicsMotorInterface',
		issubclass(Newport, EpicsMotorInterface), True)
	check('and is NOT a TwinCATMotorInterface',
		issubclass(Newport, TwinCATMotorInterface), False)
	check('a TwinCATMREAxis is a TwinCATMotorInterface',
		issubclass(TwinCATMREAxis, TwinCATMotorInterface), True)
	check('and is NOT an EpicsMotorInterface',
		issubclass(TwinCATMREAxis, EpicsMotorInterface), False)
	# The lineage the stubs in test_motorclass.py spell out link by link.
	check('Newport -> PCDSMotorBase -> EpicsMotorInterface',
		issubclass(Newport, PCDSMotorBase) and
		issubclass(PCDSMotorBase, EpicsMotorInterface), True)
	check('TwinCATMREAxis -> TwinCATAxis -> TwinCATMotorInterface',
		issubclass(TwinCATMREAxis, TwinCATAxis) and
		issubclass(TwinCATAxis, TwinCATMotorInterface), True)

	print('\n=== .DMOV lives under a different name on each ===')
	for cls in (Newport, TwinCATMREAxis):
		mine = DONE_SIGNAL[cls.__name__]
		theirs = [n for n in DONE_SIGNAL.values() if n != mine][0]
		check('{} has {}'.format(cls.__name__, mine), hasattr(cls, mine), True)
		check('{} has no {}'.format(cls.__name__, theirs),
			hasattr(cls, theirs), False)
		check('{} maps {} as a component'.format(cls.__name__, mine),
			mine in cls.component_names, True)

	# test_scanmotors.py's StubTwinCAT mirrors these three by hand, and
	# test_markers-era notes record that the PVPositioner names are the whole
	# reason the dispatch exists.
	print('\n=== the TwinCAT axis really is a PVPositioner underneath ===')
	for name in ('setpoint', 'readback', 'done'):
		check('TwinCATMREAxis maps {}'.format(name),
			name in TwinCATMREAxis.component_names, True)
	for name in ('motor_done_move', 'user_readback', 'user_setpoint'):
		check('TwinCATMREAxis does NOT map {}'.format(name),
			name in TwinCATMREAxis.component_names, False)
	check('and its done_value is still 1',
		getattr(TwinCATMREAxis, 'done_value', None), 1)

	print('\n=== the surface erfFit and MotorRow name ===')
	for cls in (Newport, TwinCATMREAxis):
		missing = [n for n in SHARED_SURFACE if not hasattr(cls, n)]
		check('{} carries every name the code calls'.format(cls.__name__),
			missing, [])

	print('\n=== mv and mvr still default wait=False ===')
	# Non-negotiable 4. move() defaults wait=True and would block the Qt slot
	# for the whole travel; a pcdsdevices release that flipped these defaults
	# would freeze the window with no error anywhere.
	for cls in (Newport, TwinCATMREAxis):
		for call in ('mv', 'mvr'):
			params = inspect.signature(getattr(cls, call)).parameters
			check('{}.{} wait default'.format(cls.__name__, call),
				params['wait'].default if 'wait' in params else 'no wait kwarg',
				False)

	print('\n=== retry_deadband is still absent ===')
	# MotorRow.arrivalTolerance and erfFit.arrivalTolerance both try it and
	# fall back to 10 um. The fallback is what actually runs, and this is the
	# measurement that says so rather than a comment claiming it.
	for cls in (Newport, TwinCATMREAxis):
		check('{} has no retry_deadband'.format(cls.__name__),
			hasattr(cls, 'retry_deadband'), False)

	print('\n=== motorClass resolves to the real classes ===')
	# Nonsense PVs on purpose: these only have to parse, and there must be
	# nothing on the other end even if this is run at UED.
	check('an MMN name gives the real Newport',
		erfFit.motorClass('TST:STUB:MMN:01:02'), Newport)
	check('an MMS name gives the real TwinCATMREAxis',
		erfFit.motorClass('TST:STUB:MMS:01'), TwinCATMREAxis)

	print('\n=== the stubs themselves ===')
	# Past this line sys.modules['pcdsdevices'] is a fake. The real classes are
	# already bound above, so the comparisons below are still against the
	# genuine article, but do not add a real-library check down here.
	import test_ismoving as ISM
	import test_motorclass as MC
	import test_scanmotors as SM

	check('test_ismoving: stand-in Newport lineage matches',
		issubclass(ISM.Newport, ISM.EpicsMotorInterface), True)
	check('test_ismoving: stand-in bases are disjoint, as the real ones are',
		issubclass(ISM.TwinCATMREAxis, ISM.EpicsMotorInterface), False)
	check('test_ismoving: FakeNewport reads the name Newport uses',
		hasattr(ISM.FakeNewport(), DONE_SIGNAL['Newport']), True)
	check('test_ismoving: FakeTwinCAT reads the name TwinCATMREAxis uses',
		hasattr(ISM.FakeTwinCAT(), DONE_SIGNAL['TwinCATMREAxis']), True)

	check('test_motorclass: stand-in Newport lineage matches',
		issubclass(MC.Newport, MC.PCDSMotorBase) and
		issubclass(MC.PCDSMotorBase, MC.EpicsMotorInterface), True)
	check('test_motorclass: stand-in TwinCAT lineage matches',
		issubclass(MC.TwinCATMREAxis, MC.TwinCATAxis) and
		issubclass(MC.TwinCATAxis, MC.TwinCATMotorInterface), True)
	check('test_motorclass: stand-in bases are disjoint',
		issubclass(MC.TwinCATMREAxis, MC.EpicsMotorInterface), False)

	# StubTwinCAT declares .VAL/.RBV/.DMOV by hand. It is allowed to carry
	# fewer components than the real class - it only needs the ones the code
	# touches - but never a name the real class does not have, which is how a
	# suite ends up green against a device layout that does not exist.
	extra = [n for n in SM.StubTwinCAT.component_names
		if n not in TwinCATMREAxis.component_names]
	check('test_scanmotors: StubTwinCAT invents no components', extra, [])
	check('test_scanmotors: StubTwinCAT done_value matches the real one',
		SM.StubTwinCAT.done_value, getattr(TwinCATMREAxis, 'done_value', None))
	# The two stand-in bases have to sit on the same side of the
	# EpicsMotor/PVPositioner split as the classes they stand for, or
	# scan.__init__'s isinstance checks are being exercised against a shape
	# that does not occur.
	check('test_scanmotors: StubMotor is on the EpicsMotor side',
		issubclass(SM.StubMotor, SM.EpicsMotor) and
		not issubclass(SM.StubMotor, SM.PVPositioner), True)
	check('test_scanmotors: StubTwinCAT is on the PVPositioner side',
		issubclass(SM.StubTwinCAT, SM.PVPositioner) and
		not issubclass(SM.StubTwinCAT, SM.EpicsMotor), True)

	# Imported last and separately: it needs Qt, and a Qt-less env should cost
	# a note rather than a failure in a suite about motor classes.
	try:
		import test_motorrow as MR
	except ImportError as e:
		print('  (test_motorrow not importable here: {})'.format(e))
	else:
		# StubMotor is a duck type, not a subclass, so the only thing to check
		# is that every name it offers the GUI is a name a real Newport has.
		# Test machinery is excluded: it is not claiming to be pcdsdevices.
		machinery = ('raiseOnMv', 'calls', 'lastStatus', 'disabled')
		claimed = [n for n in vars(MR.StubMotor)
			if not n.startswith('_') and n not in machinery]
		invented = [n for n in claimed if not hasattr(Newport, n)]
		check('test_motorrow: StubMotor invents no Newport attributes',
			invented, [])

	print('\nFAILURES: {}'.format(bad))
	return bad


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
