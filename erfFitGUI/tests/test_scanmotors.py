# Device sharing and per scan parameters in erfFit.scan
# Randy Lemons

# positions, indep and userInput belong to a scan, not to a motor, so one
# device per PV can be shared by every scan that uses it. These checks are what
# that claim means in practice: the same motor driven with different parameters
# by two scans, matched by PV rather than by list order, with the device count
# equal to the number of DISTINCT PVs rather than the number of motor slots.
#
# pcdsdevices is not installed on S3DF, so the real Newport cannot be imported.
# Newport derives from ophyd's EpicsMotor, which IS here, so that is substituted
# in sys.modules before erfFit is imported. Everything being tested - attribute
# storage, .prefix matching, instance identity - comes from ophyd's Device, so
# the substitution does not weaken the test. matplotlib is stubbed for the same
# reason erfFit_GUI never imports it: plotERF must not open a window here.
#
# Nothing connects and nothing moves. The PVs below do not exist, and
# constructing a device is not a move.

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ophyd import EpicsMotor

# erfFit now asks pcdsdevices which class a PV names, and refuses anything that
# is not an EpicsMotorInterface. The stub therefore has to supply both: a base
# standing in for EpicsMotorInterface, and a _GetMotorClass. The stub resolver
# returns the stub class for every PV, because the test PVs below carry no type
# field - resolving them the way the real table does would make them the bare
# ophyd fallback, which erfFit is now right to reject.
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

import numpy as np

import erfFit
import erfFitConfig

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# How many channel access channels one device costs. Only used to report what
# sharing saves, so a change in ophyd does not fail the suite.
CHANNELS = 19


def main():
	bad = 0

	def check(label, got, expect):
		nonlocal bad
		ok = (expect(got) if callable(expect) else got == expect)
		print('  {:<46} {:<44} {}'.format(label, repr(got), 'OK' if ok else 'BAD'))
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
			got = 'wrong type {}: {}'.format(type(e).__name__, e)
			ok = False
		print('  {:<46} {:<44} {}'.format(label, got[:44], 'OK' if ok else 'BAD'))
		if not ok:
			bad += 1

	print('\n=== getMotor: one device per PV ===')
	master = {}
	check('first call builds it', erfFit.getMotor('T:M:1', 'a', master).prefix, 'T:M:1')
	check('asking again gives the SAME object',
		erfFit.getMotor('T:M:1', 'b', master) is erfFit.getMotor('T:M:1', 'c', master), True)
	check('a different PV is a different object',
		erfFit.getMotor('T:M:2', 'd', master) is not master['T:M:1'], True)
	check('master list holds both', len(master), 2)
	# Without a dict there is nowhere to share through, so each call builds one.
	check('no dict given: standalone device', erfFit.getMotor('T:M:8', 'e').prefix, 'T:M:8')
	check('no dict given: not shared',
		erfFit.getMotor('T:M:8', 'e') is erfFit.getMotor('T:M:8', 'e'), False)

	print('\n=== parsePositions ===')
	check('scanning range is a count of POINTS', len(erfFit.parsePositions('24.1:30:24.9', True)), 30)
	check('ends are exact', (erfFit.parsePositions('24.1:30:24.9', True)[0],
		erfFit.parsePositions('24.1:30:24.9', True)[-1]), (24.1, 24.9))
	check('fixed position is a float', erfFit.parsePositions('9', False), 9.0)
	# runConfig tests 'indep is True' / 'is False', so a value that is merely
	# truthy would make a motor neither scanned nor held and it would be
	# skipped without a word. np.bool_ is the realistic way to get one.
	checkRaises('np.True_ refused', lambda: erfFit.parsePositions('1:3:2', np.True_), TypeError)
	checkRaises('indep=1 refused', lambda: erfFit.parsePositions('1:3:2', 1), TypeError)
	checkRaises('indep=None refused', lambda: erfFit.parsePositions('9', None), TypeError)
	checkRaises('range with no colons refused',
		lambda: erfFit.parsePositions('24.1', True), ValueError)

	print('\n=== scan.addMotor keeps the four lists index matched ===')
	shared = {}
	s = erfFit.scan()
	s.addMotor(['T:M:1', 'X Motor', '24.1:30:24.9', True], shared)
	s.addMotor(['T:M:2', 'Y Motor', '9', False], shared)
	check('two motors', len(s.motors), 2)
	check('positions aligned', (len(s.positions[0]), s.positions[1]), (30, 9.0))
	check('indep aligned', s.indep, [True, False])
	check('userInput aligned', s.userInput[0][1], 'X Motor')
	check('every list the same length',
		len(set([len(s.motors), len(s.positions), len(s.indep), len(s.userInput)])), 1)
	checkRaises('a 3 field userInput is refused',
		lambda: erfFit.scan().addMotor(['T:M:1', 'X', '9'], shared), TypeError)

	print('\n=== the point: two scans, same motors, different parameters ===')
	# gued_knifeEdge.txt in miniature: X scans while Y is held, then the other
	# way round. Under the old layout this needed four devices for two motors.
	master2 = {}
	first, second = erfFit.scan(), erfFit.scan()
	first.addMotor(['T:M:1', 'X', '24.1:30:24.9', True], master2)
	first.addMotor(['T:M:2', 'Y', '9', False], master2)
	second.addMotor(['T:M:1', 'X', '22', False], master2)
	second.addMotor(['T:M:2', 'Y', '11:30:12.3', True], master2)
	check('the devices are shared', first.motors[0] is second.motors[0], True)
	check('2 devices for 4 motor slots', len(master2), 2)
	check('scan 1 roles', first.indep, [True, False])
	check('scan 2 roles', second.indep, [False, True])
	check('scan 1 keeps its range after scan 2 is built', len(first.positions[0]), 30)
	check('scan 2 holds X at 22', second.positions[0], 22.0)

	print('\n=== updateMotors matches by PV, not by list order ===')
	s2 = erfFit.scan()
	order = {}
	s2.addMotor(['T:M:1', 'X', '24.1:30:24.9', True], order)
	s2.addMotor(['T:M:2', 'Y', '9', False], order)
	s2.updateMotors([['T:M:2', 'Y', '11:30:12.3', True], ['T:M:1', 'X', '22', False]])
	check('reversed input, roles still by PV', s2.indep, [False, True])
	check('positions followed the PV too', (s2.positions[0], len(s2.positions[1])), (22.0, 30))
	check('the devices were not replaced', s2.motors[0].prefix, 'T:M:1')
	check('userInput was rewritten', s2.userInput[0][2], '22')

	# Every motor is re-stamped on every call. Updating only what changed
	# would leave a stale indep=True, and runConfig would then scan that motor
	# a second time instead of holding it: a wrong scan, not an error.
	checkRaises('too few inputs refused',
		lambda: s2.updateMotors([['T:M:1', 'X', '22', False]]), ValueError)
	checkRaises('a PV not in this scan refused',
		lambda: s2.updateMotors([['T:M:9', 'Q', '1', False], ['T:M:2', 'Y', '1:3:2', True]]), ValueError)
	checkRaises('no scanning motor refused',
		lambda: s2.updateMotors([['T:M:1', 'X', '22', False], ['T:M:2', 'Y', '9', False]]), ValueError)
	checkRaises('two scanning motors refused',
		lambda: s2.updateMotors([['T:M:1', 'X', '1:3:2', True], ['T:M:2', 'Y', '1:3:2', True]]), ValueError)
	checkRaises('a flat list refused',
		lambda: s2.updateMotors(['T:M:1', 'X', '22', False]), TypeError)

	mine = ['T:M:1', 'X', '22', False]
	s2.updateMotors([mine, ['T:M:2', 'Y', '1:3:2', True]])
	check('userInput is copied, not held', s2.userInput[0] is mine, False)

	print('\n=== scan(motorList=...) ===')
	devices = [master2['T:M:1'], master2['T:M:2']]
	built = erfFit.scan(motorList=devices)
	check('a device list is accepted', len(built.motors), 2)
	# A motorList says which motors, not what they do.
	check('parameters start empty', built.indep, [None, None])
	built.updateMotors([['T:M:1', 'X', '24.1:30:24.9', True], ['T:M:2', 'Y', '9', False]])
	check('updateMotors fills them in', built.indep, [True, False])
	check('the list was copied', built.motors is devices, False)
	checkRaises('a list of strings refused',
		lambda: erfFit.scan(motorList=['T:M:1']), TypeError)
	checkRaises('a bare data PV string refused',
		lambda: erfFit.scan(dataPVList='SCOP:X'), TypeError)

	print('\n=== loadConfig on the real configs ===')
	for name in ('gued_knifeEdge.txt', 'test.txt'):
		path = os.path.join(HERE, name)
		if not os.path.isfile(path):
			continue
		built = {}
		config = erfFit.loadConfig(path, built)
		slots = sum([len(sc.motors) for sc in config])
		print('  --- {} ---'.format(name))
		check('  scans', len(config), 2)
		check('  devices built vs {} motor slots'.format(slots), len(built), 2)
		print('    sharing saved {} device(s), about {} channels'.format(
			slots - len(built), (slots - len(built)) * CHANNELS))
		check('  scans share device 0', config[0].motors[0] is config[1].motors[0], True)
		check('  roles differ per scan', config[0].indep != config[1].indep, True)
		check('  lists index matched', all([len(sc.motors) == len(sc.positions) ==
			len(sc.indep) == len(sc.userInput) for sc in config]), True)

	print('\n=== round trip: loadConfig -> saveConfig must not change the file ===')
	for name in ('gued_knifeEdge.txt', 'test.txt'):
		path = os.path.join(HERE, name)
		if not os.path.isfile(path):
			continue
		out = os.path.join('/tmp', 'roundTrip_' + name)
		erfFit.saveConfig(erfFit.loadConfig(path, {}), out)
		before = [l.strip() for l in open(path) if l.strip()]
		after = [l.strip() for l in open(out) if l.strip()]
		check('  {} survives the round trip'.format(name), after, before)
		# And again, so a format drift shows up rather than being baked in.
		again = os.path.join('/tmp', 'roundTrip2_' + name)
		erfFit.saveConfig(erfFit.loadConfig(out, {}), again)
		check('  {} stable on a second pass'.format(name),
			[l.strip() for l in open(again) if l.strip()], after)
		for junk in (out, again):
			if os.path.isfile(junk):
				os.remove(junk)

	print('\n=== the GUI shares its connected devices with a scan ===')
	# MotorDevices.devices IS the master list, so a config loaded with it
	# drives the objects the jog rows are already polling.
	path = os.path.join(HERE, 'gued_knifeEdge.txt')
	if os.path.isfile(path):
		from PyQt5.QtWidgets import QApplication
		app = QApplication.instance() or QApplication(sys.argv)
		import erfFit_GUI

		doc = erfFitConfig.parseConfigFile(path)
		panelDevices = erfFit_GUI.MotorDevices()
		fromScan1, error = panelDevices.device(doc.scans[0].motors[0])
		fromScan2, _ = panelDevices.device(doc.scans[1].motors[0])
		check('device built', fromScan1 is not None, True)
		if error:
			print('    error: {}'.format(error))
		check('the same PV in both scans is one device', fromScan1 is fromScan2, True)
		check('cache holds just that one', len(panelDevices.devices), 1)

		config = erfFit.loadConfig(path, panelDevices.devices)
		check('loadConfig reused the GUI device', config[0].motors[0] is fromScan1, True)
		check('and did not duplicate it', len(panelDevices.devices), 2)

		row = erfFit_GUI.MotorRow(doc.scans[0].motors[0], panelDevices)
		check('a MotorRow drives the shared device', row.connectMotor() is fromScan1, True)

	print('\n=== runConfig argument handling ===')
	# No scan is run: there is no EPICS route here, so these stop at the
	# argument checks before anything would move.
	checkRaises('neither a path nor a scan list refused',
		lambda: erfFit.runConfig(12345), TypeError)
	path = os.path.join(HERE, 'test.txt')
	if os.path.isfile(path):
		config = erfFit.loadConfig(path, {})
		checkRaises('saveNames length is checked',
			lambda: erfFit.runConfig(config, saveRun=True, saveNames=['just-one']), TypeError)

	print('\n=== save name derivation ===')
	# askUser=False throughout, so nothing reads stdin. Nothing here touches
	# hardware either: makeSaveNames only reads the config and the filesystem.
	path = os.path.join(HERE, 'test.txt')
	if os.path.isfile(path):
		config = erfFit.loadConfig(path, {})
		names, summary = erfFit.makeSaveNames(config, 'run1', askUser=False,
			directory=HERE)
		check('one data file per scan', len(names), len(config))
		check('scan 1 named for its motor', os.path.basename(names[0]),
			'run1_X_Motor_-_Laser_Room_Extra_DL.csv')
		check('scan 2 named for ITS motor', os.path.basename(names[1]),
			'run1_Y_Motor_-_OPA_Delay.csv')
		check('summary is the +1 file', os.path.basename(summary), 'run1_summary.csv')
		check('a .csv typed into the base is not doubled',
			os.path.basename(erfFit.makeSaveNames(config, 'run1.csv', askUser=False,
				directory=HERE)[1]), 'run1_summary.csv')

	# Two scans sweeping the SAME motor would derive one name twice, and the
	# second scan would overwrite the first scan's data.
	twin = [erfFit.scan(), erfFit.scan()]
	twinDict = {}
	twin[0].addMotor(['T:M:5', 'Shared', '1:5:2', True], twinDict)
	twin[1].addMotor(['T:M:5', 'Shared', '3:5:4', True], twinDict)
	twinNames, _ = erfFit.makeSaveNames(twin, 'dup', askUser=False, directory=HERE)
	check('same motor twice gives distinct files', twinNames[0] != twinNames[1], True)
	check('the second is suffixed', os.path.basename(twinNames[1]), 'dup_Shared_2.csv')

	checkRaises('no base name and no one to ask',
		lambda: erfFit.makeSaveNames(twin, None, askUser=False), ValueError)
	checkRaises('empty base name refused',
		lambda: erfFit.makeSaveNames(twin, '   ', askUser=False), ValueError)
	checkRaises('missing save directory caught before the run',
		lambda: erfFit.makeSaveNames(twin, 'x', askUser=False,
			directory=os.path.join(HERE, 'no-such-dir')), IOError)

	print('\n=== nameFormat and isMissingName ===')
	check('spaces become underscores', erfFit.nameFormat('Y Motor - OPA Delay'),
		'Y_Motor_-_OPA_Delay')
	# A name with a slash or a colon in it must not be able to write outside the
	# save directory or make a path no tool can open.
	check('no path separators survive', erfFit.nameFormat('X/../Y: 1'),
		lambda got: '/' not in got and ':' not in got)
	# Dashes are kept, so only the underscore runs from the spaces collapse.
	check('runs of junk collapse', erfFit.nameFormat('A   ---  B'), 'A_---_B')
	check('empty name gives empty token', erfFit.nameFormat('   '), '')
	check('blank is missing', erfFit.isMissingName(''), True)
	check('None is missing', erfFit.isMissingName(None), True)
	# 'undefined' is what addMotor and getMotor use for "not given".
	check('undefined is missing', erfFit.isMissingName('undefined'), True)
	check('any case of it is missing', erfFit.isMissingName('  UNDEFINED '), True)
	check('a real name is not missing', erfFit.isMissingName('X Motor'), False)

	print('\n=== a motor with no name falls back rather than blocking ===')
	nameless = erfFit.scan()
	nameless.addMotor(['T:M:6', 'undefined', '1:5:2', True], {})
	check('scanMotorIndex finds the swept motor', erfFit.scanMotorIndex(nameless), 0)
	check('falls back to scanN', erfFit.scanMotorName(nameless, 0, askUser=False), 'scan1')
	check('and is 1 indexed', erfFit.scanMotorName(nameless, 2, askUser=False), 'scan3')
	noSweep = erfFit.scan()
	noSweep.addMotor(['T:M:7', 'Held', '5', False], {})
	check('no scanning motor is None, not 0', erfFit.scanMotorIndex(noSweep), None)

	print('\n=== summaryRow ===')
	# Synthetic noiseless data, so the recovered parameters must be the ones put
	# in. The edge is at x0=0 deliberately: erfFit's initial guess takes x0 as
	# average(x) and does not move off it, so an edge placed anywhere else fails
	# to converge even on perfect data. That is pre-existing erfFit behaviour,
	# not something summaryRow can fix, and it is why real scans are centred.
	xs = np.linspace(-1.0, 1.0, 21)
	ys = erfFit.erfFitFunc(xs, 0.0, 1.0, 0.3, 0.0)
	vals = erfFit.erfFit(xs, ys)
	row = erfFit.summaryRow(nameless, 0, 0, '/tmp/d.csv', xs, ys, vals)
	check('row width matches the header', len(row), len(erfFit.SUMMARY_HEADER))
	check('fitOK', row[erfFit.SUMMARY_HEADER.index('fitOK')], True)
	check('w is recovered', round(row[erfFit.SUMMARY_HEADER.index('w')], 3), 0.3)
	check('rSquared is ~1', round(row[erfFit.SUMMARY_HEADER.index('rSquared')], 6), 1.0)
	check('dataFile is the basename only',
		row[erfFit.SUMMARY_HEADER.index('dataFile')], 'd.csv')

	# A negative w fits a falling edge exactly as well as a positive one, and a
	# negative spot size is meaningless. erfFit itself tends to resolve this by
	# flipping A instead, so the sign is forced here rather than fitted for.
	signed = erfFit.summaryRow(nameless, 0, 0, '/tmp/d.csv', xs, ys, (0.0, 1.0, -0.3, 0.0))
	check('w is reported as fitted', signed[erfFit.SUMMARY_HEADER.index('w')], -0.3)
	check('twoW is positive anyway',
		round(signed[erfFit.SUMMARY_HEADER.index('twoW')], 3), 0.6)
	check('fwhm is positive anyway',
		signed[erfFit.SUMMARY_HEADER.index('fwhm')] > 0, True)

	# A failed fit still gets a row saying which scan failed and why.
	dead = erfFit.summaryRow(nameless, 0, 1, '/tmp/d.csv', xs, ys, None, 'did not converge')
	check('failed fit row is still full width', len(dead), len(erfFit.SUMMARY_HEADER))
	check('failed fit is marked', dead[erfFit.SUMMARY_HEADER.index('fitOK')], False)
	check('reason is kept', dead[erfFit.SUMMARY_HEADER.index('note')], 'did not converge')
	check('scan number is 1 indexed', dead[erfFit.SUMMARY_HEADER.index('scan')], 2)

	print('\n=== saveSummary writes a readable file ===')
	out = os.path.join(HERE, 'tests', '_summary_check.csv')
	erfFit.saveSummary(out, [row, dead])
	import csv as _csv
	with open(out, 'r') as f:
		back = list(_csv.reader(f))
	os.remove(out)
	check('header plus one row per scan', len(back), 3)
	check('header is the header', back[0], erfFit.SUMMARY_HEADER)
	check('every row is full width',
		all(len(r) == len(erfFit.SUMMARY_HEADER) for r in back), True)

	print('\n=== the old per motor attributes are gone ===')
	check('no motor subclass', hasattr(erfFit, 'motor'), False)
	check('getMotor is exported', hasattr(erfFit, 'getMotor'), True)
	check('parsePositions is exported', hasattr(erfFit, 'parsePositions'), True)
	import inspect
	params = list(inspect.signature(erfFit.runMotor).parameters)
	check('runMotor takes positions second', params[1], 'positions')

	print('\nFAILURES: {}'.format(bad))
	return bad


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
