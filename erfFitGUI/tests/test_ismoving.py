# Which signal isMoving reads, per motor type
# Randy Lemons

# Both supported motor types map the motor record's .DMOV, under different
# names. A Newport is an EpicsMotor, so ophyd calls it motor_done_move. A
# TwinCAT axis is a PVPositioner driving the same tcmotor record, and the
# PVPositioner name for it is just done. erfFit.isMoving therefore dispatches on
# isinstance, and this suite is about that dispatch.
#
# It matters more than it looks. isMoving has three states, and the third is
# 'this record has no .DMOV', signalled by None, which makes both
# waitForArrival and MotorRow.updateMoveState fall back to watching whether the
# readback is still changing. Reading the wrong attribute raises AttributeError,
# which is in erfFit.motorErrors, which isMoving catches and turns into None. So
# a dispatch bug does not fail: every move on that motor quietly drops to the
# weaker completion check, on every point of every scan, and the scan still
# produces plausible looking data. Nothing else in the suite would notice.
#
# The trick used below is to give every fake device BOTH signals with OPPOSITE
# values. A Newport fake reads not-moving on motor_done_move and moving on done,
# and the TwinCAT fake has the same pair. One expected answer each, so a swapped
# branch, a reversed if/elif, or a missing isinstance cannot all pass.
#
# pcdsdevices is not installed on S3DF. The bases here are plain classes rather
# than the ophyd-derived stand-ins used in test_motorclass.py, because isMoving
# does nothing but isinstance and .get(): no record, no components, no channel
# access is involved in what is being checked. The real lineage, which is what
# makes the isinstance dispatch valid in the first place, is asserted in
# test_motorclass.py, and confirmed against live pcdsdevices by
# diagnose_motorpv.py at UED.
#
# Nothing connects and nothing moves.

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Stand-ins for the two pcdsdevices base classes. Disjoint, as the real ones
# are: EpicsMotorInterface(FltMvInterface, EpicsMotor) against
# TwinCATMotorInterface(FltMvInterface, PVPositioner).
class EpicsMotorInterface:
	pass


class Newport(EpicsMotorInterface):
	pass


class TwinCATMotorInterface:
	pass


class TwinCATMREAxis(TwinCATMotorInterface):
	pass


fakePcds = types.ModuleType('pcdsdevices')

fakeMotor = types.ModuleType('pcdsdevices.epics_motor')
fakeMotor.EpicsMotorInterface = EpicsMotorInterface
fakeMotor.Newport = Newport

fakeTwinCAT = types.ModuleType('pcdsdevices.twincat_motor')
fakeTwinCAT.TwinCATMotorInterface = TwinCATMotorInterface
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


class Signal:
	# Records every read, so a test can assert which signal was touched and
	# not merely which number came back.
	def __init__(self, value = None, raises = None):
		self.value = value
		self.raises = raises
		self.reads = 0
		self.monitorArgs = []

	def get(self, use_monitor = True, **kwargs):
		self.reads += 1
		self.monitorArgs.append(use_monitor)
		if self.raises is not None:
			raise self.raises
		return self.value


class FakeNewport(Newport):
	# .DMOV done = 1, so NOT moving. The decoy done signal says the opposite.
	def __init__(self, dmov = 1, decoy = 0, position = 5.0):
		self.motor_done_move = Signal(dmov)
		self.done = Signal(decoy)
		self.position = position


class FakeTwinCAT(TwinCATMREAxis):
	# Same pair the other way round: done is the real one here.
	def __init__(self, dmov = 1, decoy = 0, position = 5.0):
		self.done = Signal(dmov)
		self.motor_done_move = Signal(decoy)
		self.position = position


class FakeOther:
	# Neither type. Stands for the erfFitEPICS style device and for anything a
	# future motor class might be: isMoving must say 'no .DMOV' rather than
	# guess.
	def __init__(self, position = 5.0):
		self.motor_done_move = Signal(1)
		self.done = Signal(1)
		self.position = position


def main():
	bad = 0

	def check(label, got, expect):
		nonlocal bad
		ok = got is expect if expect is None or isinstance(expect, bool) else got == expect
		print('  {:<54} {:<22} {}'.format(label, repr(got), 'OK' if ok else 'BAD'))
		if not ok:
			bad += 1
			print('      expected {!r}'.format(expect))

	print('=== a Newport is read through motor_done_move ===')
	m = FakeNewport(dmov = 1, decoy = 0)
	check('.DMOV done means not moving', erfFit.isMoving(m), False)
	check('motor_done_move was read', m.motor_done_move.reads, 1)
	# The decoy is the whole point: if the TwinCAT branch ran instead, the
	# answer above would have been True and this count would be 1.
	check('done was NOT read', m.done.reads, 0)
	check('read with use_monitor=False', m.motor_done_move.monitorArgs, [False])

	m = FakeNewport(dmov = 0, decoy = 1)
	check('.DMOV not done means moving', erfFit.isMoving(m), True)
	check('still only motor_done_move', m.done.reads, 0)

	print('\n=== a TwinCAT axis is read through done ===')
	t = FakeTwinCAT(dmov = 1, decoy = 0)
	check('.DMOV done means not moving', erfFit.isMoving(t), False)
	check('done was read', t.done.reads, 1)
	check('motor_done_move was NOT read', t.motor_done_move.reads, 0)
	check('read with use_monitor=False', t.done.monitorArgs, [False])

	t = FakeTwinCAT(dmov = 0, decoy = 1)
	check('.DMOV not done means moving', erfFit.isMoving(t), True)
	check('still only done', t.motor_done_move.reads, 0)

	# use_monitor=False is not decoration. The whole move loop polls this, and
	# a cached monitor value is exactly how a move appears to finish before it
	# has started.
	print('\n=== neither type gets a cached read ===')
	check('Newport bypasses the monitor cache',
		FakeNewport().motor_done_move.monitorArgs, [])
	n = FakeNewport()
	erfFit.isMoving(n)
	check('Newport read is uncached', n.motor_done_move.monitorArgs, [False])
	w = FakeTwinCAT()
	erfFit.isMoving(w)
	check('TwinCAT read is uncached', w.done.monitorArgs, [False])

	print('\n=== an unknown device type means "no .DMOV" ===')
	# None is the third state, not an error. It is what makes the callers watch
	# the readback instead, so a device erfFit does not recognise degrades
	# rather than crashing.
	o = FakeOther()
	check('isMoving returns None', erfFit.isMoving(o), None)
	check('and read nothing at all', o.motor_done_move.reads + o.done.reads, 0)

	print('\n=== a motor error also means "no .DMOV" ===')
	for kind in (AttributeError('no such component'), TimeoutError('never connected'),
			erfFit.DisconnectedError('ioc went away')):
		m = FakeNewport()
		m.motor_done_move = Signal(raises = kind)
		check('Newport, {}'.format(type(kind).__name__), erfFit.isMoving(m), None)
		t = FakeTwinCAT()
		t.done = Signal(raises = kind)
		check('TwinCAT, {}'.format(type(kind).__name__), erfFit.isMoving(t), None)

	# A missing component is the realistic way to get here: it is exactly what
	# a wrong motor class produces. Worth knowing it is survivable on both.
	m = FakeNewport()
	del m.motor_done_move
	check('Newport with no motor_done_move at all', erfFit.isMoving(m), None)
	t = FakeTwinCAT()
	del t.done
	check('TwinCAT with no done at all', erfFit.isMoving(t), None)

	print('\n=== arrived() gets the right three states from both ===')
	# isMoving feeds arrived, which feeds waitForArrival, so the dispatch has
	# to come out right at the level the scan actually uses.
	#   True  - stopped within tolerance of target
	#   False - still moving
	#   None  - stopped, but NOT at target
	for label, make in (('Newport', FakeNewport), ('TwinCAT', FakeTwinCAT)):
		check('{}: stopped on target'.format(label),
			erfFit.arrived(make(dmov = 1, decoy = 0, position = 5.0), 5.0), True)
		check('{}: still moving'.format(label),
			erfFit.arrived(make(dmov = 0, decoy = 1, position = 5.0), 5.0), False)
		check('{}: stopped short'.format(label),
			erfFit.arrived(make(dmov = 1, decoy = 0, position = 5.0), 9.0), None)

	print('\n=== the fakes really are disjoint, so the above means something ===')
	# If these two ever shared a base, every check above would still pass while
	# testing only one branch.
	check('FakeNewport is not a TwinCATMotorInterface',
		isinstance(FakeNewport(), TwinCATMotorInterface), False)
	check('FakeTwinCAT is not an EpicsMotorInterface',
		isinstance(FakeTwinCAT(), EpicsMotorInterface), False)
	check('erfFit bound the same Newport base',
		erfFit.EpicsMotorInterface is EpicsMotorInterface, True)
	check('erfFit bound the same TwinCAT base',
		erfFit.TwinCATMotorInterface is TwinCATMotorInterface, True)

	print('\nFAILURES: {}'.format(bad))
	return bad


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
