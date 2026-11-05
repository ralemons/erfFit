# Scan execution from the GUI: config to scan objects, the worker, the gating
# Randy Lemons

# What this covers is the seam between the GUI and erfFit.runConfig, which is
# the part that cannot be checked at UED without moving a motor.
#
# Nothing here connects and nothing moves. erfFit.runConfig is replaced by a
# stub that records how it was called, so the checks are about the ARGUMENTS the
# GUI builds - the save names, the scan objects, plotRun being off, the abort
# callable - rather than about any hardware behaviour. The real runConfig is
# exercised only through its argument validation, which raises before moving.
#
# pcdsdevices is not installed on S3DF, so ophyd's EpicsMotor is substituted for
# Newport the same way test_scanmotors.py does it. matplotlib is stubbed because
# erfFit imports pyplot at module scope and the backend here is QtAgg: letting
# the real one in would put a plot window in a test run.

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ophyd import EpicsMotor

# See test_scanmotors.py for why the stub carries EpicsMotorInterface and
# _GetMotorClass as well as Newport.
class StubMotor(EpicsMotor):
	pass

fakePcds = types.ModuleType('pcdsdevices')
fakeMotor = types.ModuleType('pcdsdevices.epics_motor')
fakeMotor.Newport = StubMotor
fakeMotor.EpicsMotorInterface = EpicsMotor
fakeMotor._GetMotorClass = lambda pv: StubMotor
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

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

import erfFit
import erfFitConfig
import erfFit_GUI

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

failures = []


def check(label, got, expected):
	if callable(expected):
		ok = expected(got)
		want = 'custom check'
	else:
		ok = (got == expected)
		want = repr(expected)
	print('  {:<46} {:<42} {}'.format(label, repr(got)[:42], 'OK' if ok else 'FAILED, wanted ' + want))
	if not ok:
		failures.append(label)


def checkRaises(label, fn, expected):
	try:
		fn()
	except expected as e:
		print('  {:<46} {}: {}'.format(label, type(e).__name__, str(e)[:34]))
		return
	except Exception as e:
		print('  {:<46} FAILED, raised {} not {}'.format(label, type(e).__name__, expected.__name__))
		failures.append(label)
		return
	print('  {:<46} FAILED, raised nothing'.format(label))
	failures.append(label)


def writeConfig(path, body):
	with open(path, 'w') as f:
		f.write(body)
	return path


# A two scan config, each scan sweeping one motor and holding the other. The
# same two PVs appear in both scans, which is the normal UED H/V pair shape.
TWO_SCANS = (
	'2\n'
	'2,1,4\n'
	'T:M:X,X Motor,1:5:3,True\n'
	'T:M:Y,Y Motor,7,False\n'
	'T:D:1\n'
	'2,1,4\n'
	'T:M:X,X Motor,2,False\n'
	'T:M:Y,Y Motor,10:5:14,True\n'
	'T:D:1\n')


class FakeDevices:

	# Stands in for MotorDevices. The dict IS erfFit's master motorDict, so what
	# matters is that buildScanList puts one device per PV into it.

	def __init__(self):
		self.devices = {}
		self.error = None
		self.module = erfFit

	def load(self):
		return self.module


def main():
	app = QApplication.instance() or QApplication([])
	tmp = os.environ.get('CLAUDE_JOB_DIR', HERE)
	tmp = os.path.join(tmp, 'tmp') if os.path.isdir(os.path.join(tmp, 'tmp')) else HERE

	print('\n=== userInput round trips through the config line ===')
	path = writeConfig(os.path.join(tmp, 'tsr_two.txt'), TWO_SCANS)
	doc = erfFitConfig.parseConfigFile(path)
	check('config parsed', doc.error, None)
	motor = doc.scans[0].motors[0]
	check('scanning motor userInput', motor.userInput(),
		['T:M:X', 'X Motor', '1:5:3', True])
	check('fixed motor userInput', doc.scans[0].motors[1].userInput(),
		['T:M:Y', 'Y Motor', '7', False])
	# runConfig tests 'indep is True' / 'is False'. A numpy bool or a string
	# would make a motor neither swept nor held, and it would be skipped with no
	# data and no error.
	check('indep is a real bool', type(motor.userInput()[3]), bool)
	check('configLine still agrees with userInput', motor.configLine(),
		'T:M:X,X Motor,1:5:3,True')

	print('\n=== an edit reaches the scan, unsaved ===')
	# The whole point of building scans from the specs: the user edits the range
	# in the tree, does NOT save, and the run must use the edited number.
	motor.applyEdit('high', 9.0)
	check('userInput carries the edit', motor.userInput()[2], '1:5:9')
	check('doc knows it is edited', doc.isEdited(), True)

	devices = FakeDevices()
	scanList, error = erfFit_GUI.buildScanList(doc, erfFit, devices.devices)
	check('scan list built', error, None)
	check('one scan object per config scan', len(scanList), 2)
	check('edited high is in the positions', float(scanList[0].positions[0][-1]), 9.0)
	check('numAvgs carried over', scanList[0].numAvgs, 4)
	check('data PV carried over', scanList[0].dataPVList, ['T:D:1'])

	print('\n=== devices are shared, not rebuilt per scan ===')
	# Two scans, two motors each, but only two DISTINCT PVs. One device each is
	# the whole reason positions/indep/userInput live on the scan.
	check('one device per distinct PV', len(devices.devices), 2)
	check('scan 1 and scan 2 share the X device',
		scanList[0].motors[0] is scanList[1].motors[0], True)
	check('indep differs per scan for the same device',
		(scanList[0].indep[0], scanList[1].indep[0]), (True, False))

	print('\n=== exactly one scanning motor is required ===')
	# erfFit.loadConfig and updateMotors both insist on this, so the GUI has to
	# refuse first. Two scanning motors was the dangerous case: runConfig's loop
	# has no break, so it swept both into one filename.
	twoIndep = writeConfig(os.path.join(tmp, 'tsr_two_indep.txt'),
		'1\n2,1,4\nT:M:A,A,1:5:2,True\nT:M:B,B,3:5:4,True\nT:D:1\n')
	badDoc = erfFitConfig.parseConfigFile(twoIndep)
	problems = badDoc.validate()
	check('two scanning motors is a problem', len(problems), lambda n: n >= 1)
	check('and it says so', ' '.join(problems),
		lambda s: 'exactly 1 scanning motor' in s and 'has 2' in s)
	checkRaises('erfFit.loadConfig refuses it too',
		lambda: erfFit.loadConfig(twoIndep, {}), ValueError)

	noneIndep = writeConfig(os.path.join(tmp, 'tsr_zero_indep.txt'),
		'1\n1,1,4\nT:M:A,A,5,False\nT:D:1\n')
	zeroDoc = erfFitConfig.parseConfigFile(noneIndep)
	check('zero scanning motors is a problem too', ' '.join(zeroDoc.validate()),
		lambda s: 'exactly 1 scanning motor' in s and 'has 0' in s)

	print('\n=== save names derive before anything moves ===')
	names, summary = erfFit.makeSaveNames(scanList, 'run7', askUser=False, directory=tmp)
	check('one csv per scan', len(names), 2)
	check('named for scan 1 motor', os.path.basename(names[0]), 'run7_X_Motor.csv')
	check('named for scan 2 motor', os.path.basename(names[1]), 'run7_Y_Motor.csv')
	check('summary alongside', os.path.basename(summary), 'run7_summary.csv')

	print('\n=== the worker calls runConfig correctly ===')
	calls = []

	def fakeRunConfig(scanConfig, **kwargs):
		calls.append((scanConfig, kwargs))
		# Drive the report hook the way a real two scan run would, so the scan
		# number the worker derives can be checked.
		report = kwargs.get('report')
		if report is not None:
			for scanNum in range(len(scanConfig)):
				for point in range(3):
					report(point, 3, float(point))
		return None

	stub = types.ModuleType('erfFitStub')
	stub.runConfig = fakeRunConfig
	stub.ScanAborted = erfFit.ScanAborted
	stub.scanMotorName = erfFit.scanMotorName

	worker = erfFit_GUI.ScanWorker(stub, scanList, names, summary)

	seen = []
	worker.progress.connect(lambda s, p, t, pos: seen.append((s, p, t, pos)))
	done = []
	worker.finished.connect(lambda w, e, a: done.append((w, e, a)))

	# run() directly rather than start(): no second thread, and the signals are
	# delivered to the lambdas above synchronously.
	worker.run()

	check('runConfig called once for the whole list', len(calls), 1)
	scanArg, kwargs = calls[0]
	check('every scan handed over at once', len(scanArg), 2)
	# plotRun=False is the one that cannot be got wrong: plotERF calls
	# plt.show(), and building Qt widgets off the main thread is a hard crash.
	check('plotRun is OFF', kwargs.get('plotRun'), False)
	check('askUser is OFF', kwargs.get('askUser'), False)
	check('saveRun is on', kwargs.get('saveRun'), True)
	check('save names passed through', kwargs.get('saveNames'), names)
	check('summary name passed through', kwargs.get('summaryName'), summary)
	check('an abort callable is supplied', callable(kwargs.get('shouldAbort')), True)
	check('a report callable is supplied', callable(kwargs.get('report')), True)

	print('\n=== progress carries the scan number ===')
	# runConfig's report hook has no scan number, so the worker counts the
	# point==0 boundaries. Without that a progress bar spanning two scans
	# restarts with no way to say which scan it is on.
	check('six points reported', len(seen), 6)
	check('first three are scan 0', [s for s, p, t, pos in seen[:3]], [0, 0, 0])
	check('next three are scan 1', [s for s, p, t, pos in seen[3:]], [1, 1, 1])
	check('totals come through', seen[0][2], 3)

	print('\n=== abort is a flag, not a kill ===')
	worker2 = erfFit_GUI.ScanWorker(stub, scanList, names, summary)
	check('not aborting to begin with', worker2.shouldAbort(), False)
	worker2.requestAbort()
	check('abort requested is visible to runConfig', worker2.shouldAbort(), True)

	def abortingRunConfig(scanConfig, **kwargs):
		raise erfFit.ScanAborted('aborted at beginning of scan 1')

	stubAbort = types.ModuleType('erfFitStubAbort')
	stubAbort.runConfig = abortingRunConfig
	stubAbort.ScanAborted = erfFit.ScanAborted
	stubAbort.scanMotorName = erfFit.scanMotorName

	worker3 = erfFit_GUI.ScanWorker(stubAbort, scanList, names, summary)
	result = []
	worker3.finished.connect(lambda w, e, a: result.append((w, e, a)))
	worker3.run()
	check('abort is reported as aborted, not failed', result[0][2], True)
	check('and not as an error state', bool(result[0][1]) and result[0][2], True)

	print('\n=== a failure is reported, not raised ===')
	def brokenRunConfig(scanConfig, **kwargs):
		raise RuntimeError('T:M:X did not reach 3 within 20.0s')

	stubBroken = types.ModuleType('erfFitStubBroken')
	stubBroken.runConfig = brokenRunConfig
	stubBroken.ScanAborted = erfFit.ScanAborted
	stubBroken.scanMotorName = erfFit.scanMotorName

	worker4 = erfFit_GUI.ScanWorker(stubBroken, scanList, names, summary)
	outcome = []
	worker4.finished.connect(lambda w, e, a: outcome.append((w, e, a)))
	worker4.run()
	check('a move failure finishes with an error', outcome[0][1],
		lambda s: 'did not reach' in s)
	check('and is not called an abort', outcome[0][2], False)

	print('\n=== jogging is locked out while scanning ===')
	# The scan drives the same device objects the rows poll, so a jog mid scan
	# would move a motor out from under the sweep.
	row = erfFit_GUI.MotorRow(doc.scans[0].motors[0], devices)
	# refresh() is what normally enables the controls, off a live readback.
	# Nothing is connected here, so the readback it leaves behind is set by hand.
	row.lastPosition = 1.0
	row.setControlsEnabled(True)
	check('jog enabled when idle', row.plusButton.isEnabled(), True)
	row.setJoggingEnabled(False)
	check('plus disabled during a scan', row.plusButton.isEnabled(), False)
	check('absolute entry disabled too', row.absoluteEdit.isEnabled(), False)
	# Stop is the hardware stop, so the lock must not be what takes it away.
	# It is gated on having a device, and this row has none, so the check is
	# that setJoggingEnabled did not touch it rather than that it is on.
	check('the lock does not disable stop', row.stopButton.isEnabled(),
		row.device is not None)
	# refresh() calls setControlsEnabled every tick from the readback, so the
	# lock has to survive it.
	row.setControlsEnabled(True)
	check('a refresh does not re-enable it', row.plusButton.isEnabled(), False)
	row.setJoggingEnabled(True)
	check('released after the scan', row.plusButton.isEnabled(), True)

	# Stop all is the panel level hardware stop. Whatever the rows do, locking
	# jogging for a scan must leave it reachable.
	panel = erfFit_GUI.MotorControlPanel()
	panel.showDoc(doc)
	panel.connected = True
	panel.stopAllButton.setEnabled(True)
	panel.setJoggingEnabled(False)
	check('stop all survives the lock', panel.stopAllButton.isEnabled(), True)
	check('every row got locked', [r.joggingLocked for r in panel.rows],
		lambda got: len(got) == 2 and all(got))

	print('\n=== runConfig still refuses nonsense arguments ===')
	checkRaises('a number is not a config',
		lambda: erfFit.runConfig(12345), TypeError)
	checkRaises('too few save names',
		lambda: erfFit.runConfig(scanList, saveRun=True, saveNames=['only-one']), TypeError)

	print('\nFAILURES: {}'.format(len(failures)))
	for f in failures:
		print('  ' + f)

	return len(failures)


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
