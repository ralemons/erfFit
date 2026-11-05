# Move state transitions, driven from .DMOV and the readback rather than from
# MoveStatus. Reproduces the reported stuck-status case and its variants.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
import erfFit_GUI as G
import erfFitConfig as C


class Status:
	# done/success configurable; exception() like ophyd's.
	def __init__(self, done=False, success=True, error=None):
		self.done = done
		self.success = success
		self._error = error

	def exception(self):
		return self._error


class Stub:
	def __init__(self, position=10.0, deadband=None, hasDmov=True, status=None):
		self._p = position
		self._moving = False
		self.connected = True
		self.egu = 'mm'
		self.hasDmov = hasDmov
		self.status = status if status is not None else Status()
		if deadband is not None:
			class DB:
				def __init__(s, v): s.v = v
				def get(s): return s.v
			self.retry_deadband = DB(deadband)

	@property
	def position(self):
		return self._p

	@property
	def moving(self):
		if not self.hasDmov:
			raise RuntimeError('no DMOV on this record')
		return self._moving

	def wm(self):
		return self._p

	def check_value(self, value):
		pass

	def mv(self, position, timeout=None, wait=False, log=True):
		return self.status

	def mvr(self, delta, timeout=None, wait=False, log=True):
		return self.status

	def stop(self, success=False):
		self._moving = False


def makeRow(stub):
	spec = C.MotorSpec()
	spec.pv = 'M:1'
	spec.name = 'X Motor'
	spec.scanning = True
	devices = G.MotorDevices()
	devices.module = object()
	devices.devices['M:1'] = stub
	row = G.MotorRow(spec, devices)
	row.connectMotor()
	row.refresh()
	return row


def settle(row, ticks=3):
	# Just enough ticks to pass the settle threshold. Ticking further would
	# run down the outcome hold and show 'ready'.
	for _ in range(ticks):
		row.refresh()


def main():
	app = QApplication(sys.argv)
	bad = 0

	def check(label, got, expect):
		nonlocal bad
		ok = (expect(got) if callable(expect) else got == expect)
		print('  {:<46} {:<42} {}'.format(label, repr(got), 'OK' if ok else 'BAD'))
		if not ok:
			bad += 1

	print('\n=== the reported case: status.done never flips, 10 -> 3 ===')
	stub = Stub(10.0, status=Status(done=False))
	row = makeRow(stub)
	row.absoluteEdit.setText('3')
	row.onAbsoluteEntered()
	stub._moving = True
	for p in (8.5, 6.0, 4.5):
		stub._p = p
		row.refresh()
	check('mid move shows live distance', row.stateLabel.text(), 'moving -> 3 (1.5 to go)')
	stub._moving = False
	stub._p = 3.0
	settle(row)
	check('clears to done once DMOV says stopped', row.stateLabel.text(), 'done at 3')
	check('target cleared', row.moveTarget, None)
	check('status dropped', row.moveStatus, None)
	check('outcome is held, not overwritten next tick', (row.refresh(), row.stateLabel.text())[1], 'done at 3')
	for _ in range(row.outcomeHoldTicks() + 2):
		row.refresh()
	check('returns to ready once the hold expires', row.stateLabel.text(), 'ready')

	print('\n=== DMOV done briefly before the motor starts ===')
	stub = Stub(10.0, status=Status(done=False))
	row = makeRow(stub)
	row.absoluteEdit.setText('3')
	row.onAbsoluteEntered()
	stub._moving = False            # has not started yet
	row.refresh()
	check('does not declare done on tick 1', row.stateLabel.text(), 'moving to 3')
	row.refresh()
	check('still waiting on tick 2', row.stateLabel.text(), 'moving to 3')
	stub._moving = True             # starts moving in time
	stub._p = 7.0
	row.refresh()
	check('picks up the move', row.stateLabel.text(), 'moving -> 3 (4 to go)')
	stub._moving = False
	stub._p = 3.0
	settle(row)
	check('then completes', row.stateLabel.text(), 'done at 3')

	print('\n=== stopped short (limit, stop, or another client) ===')
	stub = Stub(10.0, status=Status(done=False))
	row = makeRow(stub)
	row.absoluteEdit.setText('3')
	row.onAbsoluteEntered()
	stub._moving = True
	stub._p = 7.2
	row.refresh()
	stub._moving = False            # stopped at 7.2, not 3
	settle(row)
	print('      message: {!r}'.format(row.stateLabel.text()))
	check('reports stopped short', row.stateLabel.text(), lambda t: t.startswith('stopped at 7.2,'))
	check('target cleared anyway', row.moveTarget, None)

	print('\n=== status reports a real failure ===')
	class LimitError(Exception):
		pass
	stub = Stub(10.0, status=Status(done=True, success=False,
		error=LimitError('Value 3 outside of range: [5, 20]')))
	row = makeRow(stub)
	row.absoluteEdit.setText('3')
	row.onAbsoluteEntered()
	stub._moving = False
	settle(row)
	print('      message: {!r}'.format(row.stateLabel.text()))
	check('failure reported', row.stateLabel.text(), lambda t: 'did not finish' in t and 'LimitError' in t)

	print('\n=== status completes normally ===')
	stub = Stub(10.0, status=Status(done=True, success=True))
	row = makeRow(stub)
	row.absoluteEdit.setText('3')
	row.onAbsoluteEntered()
	stub._moving = True
	stub._p = 5.0
	row.refresh()
	stub._moving = False
	stub._p = 3.0
	settle(row)
	check('done', row.stateLabel.text(), 'done at 3')

	print('\n=== no .DMOV: completion from the readback settling ===')
	stub = Stub(10.0, hasDmov=False, status=Status(done=False))
	row = makeRow(stub)
	row.absoluteEdit.setText('3')
	row.onAbsoluteEntered()
	for p in (8.0, 6.0, 4.0):
		stub._p = p
		row.refresh()
	check('seen as moving while changing', row.stateLabel.text(),
		lambda t: t.startswith('moving -> 3'))
	stub._p = 3.0
	settle(row, 4)
	check('completes once the readback stops', row.stateLabel.text(), 'done at 3')

	print('\n=== deadband used as the arrival tolerance ===')
	stub = Stub(10.0, deadband=0.05, status=Status(done=False))
	row = makeRow(stub)
	check('tolerance from retry_deadband', row.arrivalTolerance(), 0.1)
	row.absoluteEdit.setText('3')
	row.onAbsoluteEntered()
	stub._moving = True
	row.refresh()
	stub._moving = False
	stub._p = 3.08                  # inside 2x deadband
	settle(row)
	check('within deadband counts as arrived', row.stateLabel.text(),
		lambda t: t.startswith('done at'))

	# The fallback is the live case, not a corner: retry_deadband is not a
	# component of EpicsMotor, Newport, or anything between, so a real motor
	# raises AttributeError here exactly as this stub does. 2 um, matching
	# erfFit.arrivalTolerance.
	stub = Stub(10.0, status=Status(done=False))
	row = makeRow(stub)
	check('default tolerance with no deadband', row.arrivalTolerance(), 0.002)

	print('\n=== jog completes too ===')
	stub = Stub(10.0, status=Status(done=False))
	row = makeRow(stub)
	row.stepEdit.setText('0.5')
	row.onPlus()
	stub._moving = True
	row.refresh()
	stub._moving = False
	stub._p = 10.5
	settle(row)
	check('jog reaches done', row.stateLabel.text(), 'done at 10.5')

	print('\n=== stop during a move ===')
	stub = Stub(10.0, status=Status(done=False))
	row = makeRow(stub)
	row.absoluteEdit.setText('3')
	row.onAbsoluteEntered()
	stub._moving = True
	stub._p = 8.0
	row.refresh()
	row.onStop()
	check('stop clears the target', row.moveTarget, None)
	stub._moving = False
	settle(row)
	check('settles to ready, not stuck', row.stateLabel.text(), 'ready')

	print('\n=== idle motor moved by someone else ===')
	stub = Stub(10.0, status=Status(done=False))
	row = makeRow(stub)
	stub._moving = True
	stub._p = 12.0
	row.refresh()
	check('shows moving with no target of ours', row.stateLabel.text(), 'moving')
	stub._moving = False
	settle(row)
	check('back to ready', row.stateLabel.text(), 'ready')

	print('\n=== many idle ticks do not drift ===')
	stub = Stub(10.0, status=Status(done=False))
	row = makeRow(stub)
	for _ in range(50):
		row.refresh()
	check('still ready after 50 ticks', row.stateLabel.text(), 'ready')

	print('\n=== default camera ===')
	check('DEFAULT_CAMERA is Sample 30 Deg', G.DEFAULT_CAMERA, 'Sample 30 Deg')

    # Built without firing a camera change.
	panel = G.CameraPanel()
	check('combo starts on Sample 30 Deg', panel.cameraCombo.currentText(), 'Sample 30 Deg')
	check('prefix matches GIGE 02', panel.currentPrefix(), 'UED:GIGE:02:IMAGE1:')

	print('\nFAILURES: {}'.format(bad))
	return bad


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
