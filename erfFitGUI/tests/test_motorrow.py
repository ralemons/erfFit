# Exercises MotorRow against a stub motor.
#
# pcdsdevices is not installed on S3DF, so the real Newport cannot be imported
# here. This stub has the signatures the pcdsdevices documentation gives for
# mv, mvr, check_value, stop, position, egu, moving and connected, which lets
# the GUI's call sequencing and every guard path be tested. It does NOT test
# ophyd's own behaviour.

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
import erfFit_GUI as G
import erfFitConfig as C


class LimitError(Exception):
	pass


class MotorDisabledError(Exception):
	pass


class StubStatus:
	def __init__(self, done=False, success=True, error=None):
		self.done = done
		self.success = success
		self._error = error

	def exception(self):
		return self._error


class StubMotor:
	# Mimics the documented Newport surface.
	def __init__(self, position=10.0, low=0.0, high=20.0, egu='mm',
			connected=True, disabled=False, raiseOnMv=None):
		self._position = position
		self.low_limit = low
		self.high_limit = high
		self.limits = (low, high)
		self.egu = egu
		self.connected = connected
		self.moving = False
		self.disabled = disabled
		self.raiseOnMv = raiseOnMv
		self.calls = []
		self.lastStatus = None

	@property
	def position(self):
		return self._position

	def wm(self):
		return self._position

	def check_value(self, value):
		if self.disabled:
			raise MotorDisabledError('Motor is not enabled. Motion requests ignored')
		if not (self.low_limit <= value <= self.high_limit):
			raise LimitError('Value {} outside of range: [{}, {}]'.format(
				value, self.low_limit, self.high_limit))

	def mv(self, position, timeout=None, wait=False, log=True):
		self.calls.append(('mv', position, wait))
		if self.raiseOnMv:
			raise self.raiseOnMv
		self.lastStatus = StubStatus()
		return self.lastStatus

	def mvr(self, delta, timeout=None, wait=False, log=True):
		self.calls.append(('mvr', delta, wait))
		if self.raiseOnMv:
			raise self.raiseOnMv
		self.lastStatus = StubStatus()
		return self.lastStatus

	def stop(self, success=False):
		self.calls.append(('stop',))

	def move(self, position, wait=True, **kwargs):
		# Present so the test can prove the GUI never calls it, since its
		# wait=True default would block the UI thread.
		self.calls.append(('move', position, wait))
		raise AssertionError('GUI must not call move(); use mv()/mvr()')


def makeRow(stub, name='X Motor', pv='UED:USR:MMN:04:03'):
	spec = C.MotorSpec()
	spec.pv = pv
	spec.name = name
	spec.scanning = True

	devices = G.MotorDevices()
	devices.module = object()                 # pretend the import worked
	devices.devices[pv] = stub

	row = G.MotorRow(spec, devices)
	row.connectMotor()
	row.refresh()
	return row


def check(label, got, expect):
	ok = got == expect
	print('  {:<44} {:<28} {}'.format(label, repr(got), 'OK' if ok else 'BAD expected ' + repr(expect)))
	return 0 if ok else 1


def main():
	app = QApplication(sys.argv)
	bad = 0

	print('\n=== readback, units, controls enabled ===')
	stub = StubMotor(position=12.3456)
	row = makeRow(stub)
	bad += check('readback shows position with units', row.readback.text(), '12.3456 mm')
	bad += check('absolute box enabled', row.absoluteEdit.isEnabled(), True)
	bad += check('plus enabled', row.plusButton.isEnabled(), True)
	bad += check('stop enabled', row.stopButton.isEnabled(), True)
	bad += check('state ready', row.stateLabel.text(), 'ready')

	print('\n=== absolute move calls mv(target) and nothing else ===')
	stub = StubMotor(position=10.0)
	row = makeRow(stub)
	row.absoluteEdit.setText('15.5')
	row.onAbsoluteEntered()
	bad += check('mv called once with target', stub.calls, [('mv', 15.5, False)])
	bad += check('absolute box cleared after send', row.absoluteEdit.text(), '')
	bad += check('moveTarget recorded', row.moveTarget, 15.5)
	bad += check('status stored', row.moveStatus is stub.lastStatus, True)

	print('\n=== relative move calls mvr(delta), not mv ===')
	stub = StubMotor(position=10.0)
	row = makeRow(stub)
	row.stepEdit.setText('0.25')
	row.onPlus()
	bad += check('plus  -> mvr(+0.25)', stub.calls, [('mvr', 0.25, False)])
	stub.calls = []
	row.onMinus()
	bad += check('minus -> mvr(-0.25)', stub.calls, [('mvr', -0.25, False)])

	print('\n=== move() is never used (its wait=True would block) ===')
	stub = StubMotor(position=10.0)
	row = makeRow(stub)
	row.absoluteEdit.setText('11'); row.onAbsoluteEntered()
	row.stepEdit.setText('0.1'); row.onPlus(); row.onMinus()
	usedMove = any(c[0] == 'move' for c in stub.calls)
	bad += check('move() not called', usedMove, False)
	allNonBlocking = all(c[2] is False for c in stub.calls if c[0] in ('mv', 'mvr'))
	bad += check('every mv/mvr was non-blocking', allNonBlocking, True)

	print('\n=== limit violation refused before any move ===')
	stub = StubMotor(position=10.0, low=0.0, high=20.0)
	row = makeRow(stub)
	row.absoluteEdit.setText('99')
	row.onAbsoluteEntered()
	bad += check('no move issued', stub.calls, [])
	print('      message: {!r}'.format(row.stateLabel.text()))
	bad += check('reports refusal', row.stateLabel.text().startswith('refused:'), True)

	print('\n=== jog past the limit refused ===')
	stub = StubMotor(position=19.9, low=0.0, high=20.0)
	row = makeRow(stub)
	row.stepEdit.setText('1.0')
	row.onPlus()
	bad += check('no move issued', stub.calls, [])
	bad += check('reports refusal', row.stateLabel.text().startswith('refused:'), True)

	print('\n=== disabled motor refused ===')
	stub = StubMotor(position=10.0, disabled=True)
	row = makeRow(stub)
	row.absoluteEdit.setText('11')
	row.onAbsoluteEntered()
	bad += check('no move issued', stub.calls, [])
	print('      message: {!r}'.format(row.stateLabel.text()))
	bad += check('mentions MotorDisabledError', 'MotorDisabledError' in row.stateLabel.text(), True)

	print('\n=== mv raising is caught ===')
	stub = StubMotor(position=10.0, raiseOnMv=RuntimeError('channel access failure'))
	row = makeRow(stub)
	row.absoluteEdit.setText('11')
	row.onAbsoluteEntered()
	print('      message: {!r}'.format(row.stateLabel.text()))
	bad += check('reports failure', row.stateLabel.text().startswith('move failed:'), True)
	bad += check('no stale target', row.moveTarget, None)

	print('\n=== failed status is surfaced (mv swallows LimitError) ===')
	stub = StubMotor(position=10.0)
	row = makeRow(stub)
	row.absoluteEdit.setText('15'); row.onAbsoluteEntered()
	stub.lastStatus.done = True
	stub.lastStatus.success = False
	stub.lastStatus._error = LimitError('Value 15 outside of range: [0, 12]')
	stub.moving = False
	for _ in range(3):
		row.refresh()
	print('      message: {!r}'.format(row.stateLabel.text()))
	bad += check('reports unfinished move', row.stateLabel.text().startswith('move did not finish'), True)
	bad += check('status cleared', row.moveStatus, None)

	print('\n=== successful completion ===')
	stub = StubMotor(position=10.0)
	row = makeRow(stub)
	row.absoluteEdit.setText('15'); row.onAbsoluteEntered()
	stub._position = 15.0
	stub.lastStatus.done = True
	stub.lastStatus.success = True
	stub.moving = False
	for _ in range(3):
		row.refresh()
	print('      message: {!r}'.format(row.stateLabel.text()))
	bad += check('reports done', row.stateLabel.text().startswith('done at'), True)
	bad += check('target cleared', row.moveTarget, None)

	print('\n=== in-flight progress display ===')
	stub = StubMotor(position=10.0)
	row = makeRow(stub)
	row.absoluteEdit.setText('15'); row.onAbsoluteEntered()
	stub.moving = True
	stub._position = 12.0
	row.refresh()
	print('      message: {!r}'.format(row.stateLabel.text()))
	bad += check('shows remaining distance', '3 to go' in row.stateLabel.text(), True)

	print('\n=== stop ===')
	stub = StubMotor(position=10.0)
	row = makeRow(stub)
	row.absoluteEdit.setText('15'); row.onAbsoluteEntered()
	stub.calls = []
	row.onStop()
	bad += check('stop called', stub.calls, [('stop',)])
	bad += check('target cleared', row.moveTarget, None)
	bad += check('status cleared', row.moveStatus, None)

	print('\n=== guards: no device, bad input, no step ===')
	spec = C.MotorSpec(); spec.pv = 'X:Y'; spec.name = 'n'; spec.scanning = True
	row = G.MotorRow(spec, G.MotorDevices())
	row.requestMove(5.0, 'absolute')
	bad += check('move with no device', row.stateLabel.text(), 'not connected')
	row.jog(1)
	bad += check('jog with no step/device', row.stateLabel.text() in ('not connected', 'set a step size first'), True)
	row.onStop()   # must not raise

	stub = StubMotor(position=10.0)
	row = makeRow(stub)
	row.absoluteEdit.setText('not a number')
	row.onAbsoluteEntered()
	bad += check('bad absolute value', row.stateLabel.text(), 'bad position value')
	bad += check('no move issued', stub.calls, [])
	row.stepEdit.setText('xyz')
	row.onPlus()
	bad += check('bad step value', row.stateLabel.text(), 'set a step size first')
	bad += check('still no move', stub.calls, [])

	print('\n=== disconnected device ===')
	stub = StubMotor(position=10.0, connected=False)
	row = makeRow(stub)
	bad += check('refresh reports not connected', row.refresh(), False)
	bad += check('readback blanked', row.readback.text(), '--')
	bad += check('controls disabled', row.plusButton.isEnabled(), False)
	bad += check('stop still available', row.stopButton.isEnabled(), True)

	print('\n=== device that raises on every access ===')
	class Hostile:
		connected = True
		def __getattr__(self, name):
			raise RuntimeError('IOC went away')
	spec = C.MotorSpec(); spec.pv = 'H:1'; spec.name = 'h'; spec.scanning = True
	devices = G.MotorDevices(); devices.module = object(); devices.devices['H:1'] = Hostile()
	row = G.MotorRow(spec, devices)
	row.connectMotor()
	try:
		row.refresh()
		print('  {:<44} {:<28} OK'.format('refresh survives a hostile device', repr(row.stateLabel.text())))
	except Exception as e:
		bad += 1
		print('  refresh RAISED {}: {}'.format(type(e).__name__, e))

	print('\n=== missing or odd egu ===')
	row = makeRow(StubMotor(position=1.0, egu=None))
	bad += check('egu None -> no units', row.readback.text(), '1')
	row = makeRow(StubMotor(position=1.0, egu=''))
	bad += check('egu empty -> no units', row.readback.text(), '1')

	class RaisingEgu:
		connected = True
		moving = False
		position = 2.5
		def check_value(self, value): pass
		def mv(self, position, timeout=None, wait=False, log=True): return StubStatus()
		def mvr(self, delta, timeout=None, wait=False, log=True): return StubStatus()
		def stop(self, success=False): pass
		@property
		def egu(self):
			raise RuntimeError('no egu field on this record')
	row = makeRow(RaisingEgu())
	bad += check('egu raising -> no units, no crash', row.readback.text(), '2.5')

	print('\n=== import failure path ===')
	devices = G.MotorDevices()
	devices.error = "No module named 'pcdsdevices'"
	spec = C.MotorSpec(); spec.pv = 'A:B'; spec.name = 'a'; spec.scanning = True
	row = G.MotorRow(spec, devices)
	result = row.connectMotor()
	bad += check('connectMotor returns None', result, None)
	bad += check('row reports it', row.stateLabel.text(), 'cannot connect')
	bad += check('refresh is a no-op', row.refresh(), False)

	print('\n=== deviceName sanitizing ===')
	for raw, expect in [('X Motor', 'X_Motor'), ('', None), ('9lives', 'motor_9lives'),
			('a-b.c', 'a_b_c'), ('(unnamed) 11:03', 'unnamed__11_03')]:
		spec = C.MotorSpec(); spec.name = raw; spec.pv = 'UED:USR:MMN:04:03'
		got = G.deviceName(spec)
		isIdent = got.isidentifier()
		if expect is None:
			print('  {:<44} {:<28} {}'.format('name %r' % raw, repr(got), 'OK identifier' if isIdent else 'BAD'))
			bad += 0 if isIdent else 1
		else:
			bad += check('name %r' % raw, got, expect)
		if not isIdent:
			bad += 1
			print('      NOT a valid python identifier')

	print('\nFAILURES: {}'.format(bad))
	return bad


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
