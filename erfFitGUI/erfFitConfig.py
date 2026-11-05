# Standalone parser for erfFit scan config files
# Randy Lemons

# This module deliberately imports NOTHING but os.
#
# It does not import erfFit, pcdsdevices, epics, or Qt. That is the whole point:
# erfFit.loadConfig() calls scan.addMotor(), which constructs motor(Newport)
# objects, so simply LOADING a config file with erfFit connects to live EPICS
# motors. A display-only viewer must never do that, so it parses the text itself.
#
# This parser is also more tolerant than erfFit.loadConfig(). Its clean() helper
# assumes exactly 4 comma fields per motor line, so the legacy 3-field format in
# solidState_KnifeEdge.txt raises IndexError there. Here that format loads, with
# a warning.
#
# Config file format:
#	line 1			number of scans
#	then per scan:
#		numMotors,numDataPVs,numAvgs
#		numMotors lines of	motorPV,motorName,positions,indep
#			indep == True	-> this motor scans, positions is low:numSteps:high
#			indep == False	-> this motor is fixed, positions is a single value
#		numDataPVs lines, each a single PV name

import os


class MotorSpec:

	# EDITED_FIELDS lists what the GUI is allowed to change. Each edit is
	# recorded in self.edited so the tree can mark it and so writing the file
	# back out is a question of regenerating the line from current values.
	EDITED_FIELDS = ('pv', 'name', 'low', 'high', 'numSteps', 'value')

	def __init__(self):
		self.pv = ''
		self.name = ''
		self.scanning = False
		self.raw = ''
		self.low = None
		self.high = None
		self.numSteps = None
		self.stepSize = None
		self.value = None
		self.nameSynth = False
		self.warnings = []
		self.edited = set()
		self.original = {}

	def rememberOriginal(self):
		# Called once after parsing, so edits can be compared and reverted.
		self.original = dict([(f, getattr(self, f)) for f in self.EDITED_FIELDS])

	def isEdited(self):
		return bool(self.edited)

	def originalText(self, field):
		value = self.original.get(field)
		if value is None:
			return ''
		if field in ('pv', 'name'):
			return str(value)
		return fmtNum(value)

	def applyEdit(self, field, value):
		# Sets a field, tracks whether it still differs from the loaded value,
		# and recomputes the derived step size. Returns a list of problems,
		# empty when the edit is good.
		if field not in self.EDITED_FIELDS:
			return ['{} is not editable'.format(field)]

		setattr(self, field, value)

		if field in self.original and value == self.original[field]:
			self.edited.discard(field)
		else:
			self.edited.add(field)

		self.refresh()
		return self.validate()

	def revertEdits(self):
		for field in self.EDITED_FIELDS:
			if field in self.original:
				setattr(self, field, self.original[field])
		self.edited = set()
		self.refresh()

	def refresh(self):
		# Recompute step size and the raw text from the current values, so the
		# display and a saved file agree with what was typed.
		if self.scanning:
			if self.low is not None and self.high is not None \
					and self.numSteps is not None and self.numSteps > 1:
				self.stepSize = (self.high - self.low) / (self.numSteps - 1)
			else:
				self.stepSize = None
			self.raw = self.rangeText()
		else:
			self.stepSize = None
			if self.value is not None:
				self.raw = fmtNum(self.value)

	def rangeText(self):
		if self.low is None or self.high is None or self.numSteps is None:
			return self.raw
		return '{}:{}:{}'.format(fmtNum(self.low), self.numSteps, fmtNum(self.high))

	def validate(self):
		# Problems that should block a save. Kept separate from self.warnings,
		# which holds whatever the original file had wrong.
		problems = []
		if not self.pv:
			problems.append('motor PV is empty')
		elif ',' in self.pv:
			problems.append('motor PV cannot contain a comma')
		if self.scanning:
			if self.low is None or self.high is None or self.numSteps is None:
				problems.append('scan range is incomplete')
			else:
				if self.numSteps < 2:
					problems.append('number of steps must be at least 2')
				if self.low == self.high:
					problems.append('start and end are both {}'.format(fmtNum(self.low)))
		elif self.value is None:
			problems.append('fixed position is not a number')
		return problems

	def positionField(self):
		# The positions as a config file writes them: 'low:numSteps:high' for a
		# scanning motor, a plain number for a fixed one.
		return self.rangeText() if self.scanning else fmtNum(self.value)

	def userInput(self):
		# The [pv, name, positions, indep] list erfFit.scan.addMotor and
		# updateMotors take. Built from the CURRENT values, so running a config
		# that has been edited in the GUI runs what is on screen rather than
		# what the file said.
		#
		# self.scanning is a real bool from parseMotorLine, which matters:
		# runConfig tests 'indep is True' and 'indep is False', so a merely
		# truthy value would make a motor neither scanned nor held.
		return [self.pv, self.saveName(), self.positionField(), self.scanning]

	def configLine(self):
		# The motor line as it should appear in a config file. Always written in
		# the 4 field form, which is what erfFit.loadConfig expects, so saving a
		# legacy 3 field file upgrades it.
		return '{},{},{},{}'.format(*self.userInput())

	def saveName(self):
		# The name to write to a file. A synthesized placeholder like
		# '(unnamed) 11:03' is display text, not data, so it is cleaned up
		# rather than written out verbatim. Commas would split the line on the
		# next read, so they go too.
		name = self.name
		if self.nameSynth and 'pv' not in self.edited and 'name' not in self.edited:
			name = 'Motor ' + self.pv.split('.')[0].split(':')[-1]
		name = name.replace(',', ' ').strip()
		return name if name else 'undefined'

	def positionText(self):
		if self.scanning:
			if self.low is None:
				return self.raw
			return '{} -> {}'.format(fmtNum(self.low), fmtNum(self.high))
		if self.value is None:
			return self.raw
		return fmtNum(self.value)

	def detailText(self):
		if self.scanning:
			if self.numSteps is None:
				return 'unparsed range'
			if self.stepSize is None:
				return '{} pts'.format(self.numSteps)
			return '{} pts, step {}'.format(self.numSteps, fmtNum(self.stepSize, 4))
		return 'held'

	def roleText(self):
		if self.scanning:
			return 'SCANNING'
		return 'fixed'


class ScanSpec:

	def __init__(self, index):
		self.index = index
		self.numAvgs = 0
		self.motors = []
		self.dataPVs = []
		self.warnings = []
		self.editedDataPVs = set()
		self.editedNumAvgs = False
		self.originalDataPVs = []
		self.originalNumAvgs = 0

	def rememberOriginal(self):
		self.originalDataPVs = list(self.dataPVs)
		self.originalNumAvgs = self.numAvgs
		for motor in self.motors:
			motor.rememberOriginal()

	def setDataPV(self, index, value):
		if index < 0 or index >= len(self.dataPVs):
			return ['no data PV at position {}'.format(index + 1)]
		self.dataPVs[index] = value
		if index < len(self.originalDataPVs) and value == self.originalDataPVs[index]:
			self.editedDataPVs.discard(index)
		else:
			self.editedDataPVs.add(index)
		if not value:
			return ['data PV is empty']
		if ',' in value:
			return ['data PV cannot contain a comma']
		return []

	def setNumAvgs(self, value):
		self.numAvgs = value
		self.editedNumAvgs = (value != self.originalNumAvgs)
		return [] if value >= 1 else ['number of averages must be at least 1']

	def isDataPVEdited(self, index):
		return index in self.editedDataPVs

	def isEdited(self):
		return bool(self.editedDataPVs) or self.editedNumAvgs \
			or any([m.isEdited() for m in self.motors])

	def revertEdits(self):
		self.dataPVs = list(self.originalDataPVs)
		self.numAvgs = self.originalNumAvgs
		self.editedDataPVs = set()
		self.editedNumAvgs = False
		for motor in self.motors:
			motor.revertEdits()

	def validate(self):
		problems = []
		for motorNum, motor in enumerate(self.motors):
			for problem in motor.validate():
				problems.append('scan {}, motor {}: {}'.format(self.index, motorNum + 1, problem))
		for pvNum, dataPV in enumerate(self.dataPVs):
			if not dataPV:
				problems.append('scan {}, data PV {}: is empty'.format(self.index, pvNum + 1))
			elif ',' in dataPV:
				problems.append('scan {}, data PV {}: cannot contain a comma'.format(self.index, pvNum + 1))
		if self.numAvgs < 1:
			problems.append('scan {}: number of averages must be at least 1'.format(self.index))

		# erfFit.loadConfig and scan.updateMotors both insist on exactly one
		# scanning motor, so a config that breaks it cannot be run at all. Said
		# here too, because the GUI has to refuse to run it before erfFit raises.
		# Two scanning motors used to be the worse case: runConfig's loop has no
		# break, so it would sweep both into the same filename and the second
		# would overwrite the first.
		scanning = len(self.scanningMotors())
		if scanning != 1:
			problems.append('scan {}: needs exactly 1 scanning motor, has {}'.format(
				self.index, scanning))

		return problems

	def scanningMotors(self):
		return [m for m in self.motors if m.scanning]

	def fixedMotors(self):
		return [m for m in self.motors if not m.scanning]

	def summary(self):
		scanning = self.scanningMotors()
		if len(scanning) == 0:
			which = 'nothing scanning'
		else:
			which = 'scanning ' + ', '.join([m.name for m in scanning])
		return '{}, {} avgs'.format(which, self.numAvgs)


class ConfigDoc:

	def __init__(self, path):
		self.path = path
		self.fileName = os.path.basename(path) if path else ''
		self.numScansDeclared = 0
		self.scans = []
		self.warnings = []
		self.error = None

	def ok(self):
		return self.error is None

	def rememberOriginal(self):
		for scan in self.scans:
			scan.rememberOriginal()

	def isEdited(self):
		return any([s.isEdited() for s in self.scans])

	def revertEdits(self):
		for scan in self.scans:
			scan.revertEdits()

	def validate(self):
		problems = []
		for scan in self.scans:
			problems.extend(scan.validate())
		return problems

	def editSummary(self):
		motors = 0
		dataPVs = 0
		avgs = 0
		for scan in self.scans:
			motors += len([m for m in scan.motors if m.isEdited()])
			dataPVs += len(scan.editedDataPVs)
			if scan.editedNumAvgs:
				avgs += 1
		parts = []
		if motors:
			parts.append('{} motor(s)'.format(motors))
		if dataPVs:
			parts.append('{} data PV(s)'.format(dataPVs))
		if avgs:
			parts.append('{} averages value(s)'.format(avgs))
		return ', '.join(parts)

	def allWarnings(self):
		# File level warnings first, then per scan, then per motor, each tagged
		# with where it came from so the GUI can show one flat list.
		out = list(self.warnings)
		for s in self.scans:
			for w in s.warnings:
				out.append('Scan {}: {}'.format(s.index, w))
			for m in s.motors:
				for w in m.warnings:
					out.append('Scan {}, {}: {}'.format(s.index, m.pv, w))
		return out


def fmtNum(val, digits=6):
	# Trim trailing zeros so 24.900000 shows as 24.9 and 0.027586 as 0.027586.
	if val is None:
		return ''
	text = '{:.{}f}'.format(float(val), digits).rstrip('0').rstrip('.')
	return text if text not in ('', '-') else '0'


def synthesizeName(pv):
	# Legacy 3 field lines have no motor name, so build a readable one from the
	# PV. UED:USR:MMN:11:03.VAL -> '(unnamed) 11:03'
	base = pv.split('.')[0]
	parts = [p for p in base.split(':') if p]
	if len(parts) >= 2:
		return '(unnamed) ' + ':'.join(parts[-2:])
	if len(parts) == 1:
		return '(unnamed) ' + parts[0]
	return '(unnamed)'


def splitFields(line):
	return [f.strip().replace("'", '').replace('"', '') for f in line.split(',')]


def parseMotorLine(line):
	# Returns a MotorSpec. Tolerates both the current 4 field format and the
	# legacy 3 field format that has no motor name.
	motor = MotorSpec()
	fields = splitFields(line)

	if len(fields) >= 4:
		pv, name, raw, indep = fields[0], fields[1], fields[2], fields[3]
		if len(fields) > 4:
			motor.warnings.append('{} comma fields on motor line, expected 4; extras ignored'.format(len(fields)))
	elif len(fields) == 3:
		pv, raw, indep = fields[0], fields[1], fields[2]
		name = synthesizeName(pv)
		motor.nameSynth = True
	else:
		motor.pv = line.strip()
		motor.name = '(unparsed)'
		motor.raw = line.strip()
		motor.warnings.append('could not parse motor line, expected 3 or 4 comma fields, got {}'.format(len(fields)))
		return motor

	motor.pv = pv
	motor.name = name if name else synthesizeName(pv)
	motor.raw = raw

	flag = indep.strip().lower()
	if flag == 'true':
		motor.scanning = True
	elif flag == 'false':
		motor.scanning = False
	else:
		# Default to fixed. Showing a mis-parsed motor as NOT scanning is the
		# safer of the two wrong answers.
		motor.scanning = False
		motor.warnings.append("indep flag is '{}', expected True or False; treating as fixed".format(indep))

	if motor.scanning:
		parseScanRange(motor)
	else:
		parseFixedValue(motor)

	return motor


def parseScanRange(motor):
	parts = motor.raw.split(':')
	if len(parts) != 3:
		motor.warnings.append("scanning motor positions '{}' is not low:numSteps:high".format(motor.raw))
		return
	try:
		motor.low = float(parts[0])
		motor.numSteps = int(parts[1])
		motor.high = float(parts[2])
	except ValueError:
		motor.low = None
		motor.numSteps = None
		motor.high = None
		motor.warnings.append("could not read numbers from range '{}'".format(motor.raw))
		return

	# erfFit builds positions with np.linspace(low, high, numSteps), so numSteps
	# is a count of points, not of intervals.
	if motor.numSteps < 2:
		motor.warnings.append('numSteps is {}, needs at least 2 points for a scan'.format(motor.numSteps))
	else:
		motor.stepSize = (motor.high - motor.low) / (motor.numSteps - 1)

	if motor.high == motor.low:
		motor.warnings.append('scan start and end are both {}'.format(fmtNum(motor.low)))


def parseFixedValue(motor):
	if ':' in motor.raw:
		motor.warnings.append("fixed motor has a range '{}'; erfFit would fail converting this to a float".format(motor.raw))
		return
	try:
		motor.value = float(motor.raw)
	except ValueError:
		motor.warnings.append("could not read fixed position '{}' as a number".format(motor.raw))


def parseConfigFile(path):
	# Returns a ConfigDoc. Never raises on bad file content. Fatal problems land
	# in doc.error and anything parsed before the problem is kept, so a half
	# edited config is still inspectable.
	doc = ConfigDoc(path)

	if not path:
		doc.error = 'No file given'
		return doc

	if path.endswith('.pkl'):
		# Unpickling rebuilds erfFit.scan and erfFit.motor instances, which
		# imports pcdsdevices and constructs live EPICS motors. Not safe here.
		doc.error = 'Pickle configs are not supported by the viewer, because loading one would construct live motor objects. Use the .txt form.'
		return doc

	if not os.path.isfile(path):
		doc.error = 'File not found: {}'.format(path)
		return doc

	try:
		with open(path, 'r') as f:
			rawLines = f.readlines()
	except IOError as e:
		doc.error = 'Could not read file: {}'.format(e)
		return doc

	lines = [l.strip() for l in rawLines if l.strip()]
	if not lines:
		doc.error = 'File is empty'
		return doc

	try:
		doc.numScansDeclared = int(splitFields(lines[0])[0])
	except (ValueError, IndexError):
		doc.error = "First line should be the number of scans, got '{}'".format(lines[0])
		return doc

	if doc.numScansDeclared < 1:
		doc.error = 'File declares {} scans'.format(doc.numScansDeclared)
		return doc

	lineNum = 1
	for ii in range(doc.numScansDeclared):
		if lineNum >= len(lines):
			doc.error = 'File ended early, expected {} scans but only found {}'.format(doc.numScansDeclared, ii)
			break

		scan = ScanSpec(ii + 1)
		doc.scans.append(scan)

		header = splitFields(lines[lineNum])
		lineNum += 1
		try:
			numMotors = int(header[0])
			numDataPVs = int(header[1])
			scan.numAvgs = int(header[2])
		except (ValueError, IndexError):
			doc.error = "Scan {} header should be numMotors,numDataPVs,numAvgs, got '{}'".format(scan.index, ','.join(header))
			break

		truncated = False
		for jj in range(numMotors):
			if lineNum >= len(lines):
				scan.warnings.append('header declares {} motors but the file ended after {}'.format(numMotors, jj))
				truncated = True
				break
			scan.motors.append(parseMotorLine(lines[lineNum]))
			lineNum += 1

		if truncated:
			doc.error = 'File ended early while reading scan {}'.format(scan.index)
			break

		for jj in range(numDataPVs):
			if lineNum >= len(lines):
				scan.warnings.append('header declares {} data PVs but the file ended after {}'.format(numDataPVs, jj))
				truncated = True
				break
			scan.dataPVs.append(lines[lineNum])
			lineNum += 1

		if truncated:
			doc.error = 'File ended early while reading scan {}'.format(scan.index)
			break

		checkScan(scan)

	if doc.error is None and lineNum < len(lines):
		doc.warnings.append('{} unused line(s) after the last declared scan'.format(len(lines) - lineNum))

	legacy = [m for s in doc.scans for m in s.motors if m.nameSynth]
	if legacy:
		doc.warnings.append(
			'{} motor line(s) use the legacy 3 field format with no motor name. '
			'Names shown here were generated from the PV. Note erfFit.runConfig '
			'raises IndexError on this file as written. Saving from the GUI '
			'rewrites them in the 4 field form, which fixes that.'.format(len(legacy)))

	doc.rememberOriginal()

	return doc


def formatConfig(doc):
	# Renders a ConfigDoc back to the config file text, in the format
	# erfFit.loadConfig expects. Mirrors erfFit.saveConfig.
	lines = [str(len(doc.scans))]

	for scan in doc.scans:
		lines.append('{},{},{}'.format(len(scan.motors), len(scan.dataPVs), scan.numAvgs))
		for motor in scan.motors:
			lines.append(motor.configLine())
		for dataPV in scan.dataPVs:
			lines.append(dataPV)

	return '\n'.join(lines) + '\n'


def saveConfigFile(doc, path):
	# Writes the config out. Returns an error string, or None on success.
	# Validation is the caller's job, but it is repeated here so no code path
	# can quietly write a file that erfFit would choke on.
	problems = doc.validate()
	if problems:
		return 'Config has problems that must be fixed first:\n  ' + '\n  '.join(problems)

	if not doc.scans:
		return 'Nothing to save, this config has no scans'

	try:
		with open(path, 'w') as f:
			f.write(formatConfig(doc))
	except IOError as e:
		return 'Could not write file: {}'.format(e)

	return None


def checkScan(scan):
	# Cross checks that catch config mistakes before a scan is ever run.
	scanning = scan.scanningMotors()

	if len(scanning) == 0:
		scan.warnings.append('no motor has indep=True, so erfFit would move the fixed motors and collect no data')
	elif len(scanning) > 1:
		scan.warnings.append('{} motors have indep=True; erfFit runs a separate scan, fit and save prompt for each'.format(len(scanning)))

	if len(scan.dataPVs) == 0:
		scan.warnings.append('no data PV, so there is nothing to read while scanning')
	elif len(scan.dataPVs) > 1:
		scan.warnings.append('{} data PVs listed, but erfFit.runConfig only ever reads the first one'.format(len(scan.dataPVs)))

	if scan.numAvgs < 1:
		scan.warnings.append('numAvgs is {}'.format(scan.numAvgs))


def describeConfig(doc):
	# Plain text dump, handy for checking a file without starting the GUI.
	out = []
	if doc.fileName:
		out.append('{}: {} scan(s) declared'.format(doc.fileName, doc.numScansDeclared))
	if doc.error:
		out.append('ERROR: ' + doc.error)
	for scan in doc.scans:
		out.append('--- Scan {} --- {}'.format(scan.index, scan.summary()))
		for motor in scan.motors:
			out.append('  {:<24} {:<18} {:<9} {:<16} {}'.format(
				motor.pv, motor.name, motor.roleText(),
				motor.positionText(), motor.detailText()))
		for pvNum, dataPV in enumerate(scan.dataPVs):
			note = '' if pvNum == 0 else '  (unused by erfFit)'
			out.append('  data PV {}: {}{}'.format(pvNum + 1, dataPV, note))
	for warning in doc.allWarnings():
		out.append('  ! ' + warning)
	return '\n'.join(out)


if __name__ == '__main__':
	import sys

	if len(sys.argv) > 1:
		paths = sys.argv[1:]
	else:
		# No arguments: describe every config in the config directory. Skip
		# notes and readmes, which are .txt but are not configs.
		import erfFitPaths
		here = erfFitPaths.configDir()
		print('Reading configs from {}\n'.format(here))
		skip = ('readme', 'notes')
		paths = sorted([os.path.join(here, f) for f in os.listdir(here)
			if f.endswith('.txt') and not f.lower().startswith(skip)])

	for path in paths:
		print(describeConfig(parseConfigFile(path)))
		print('')
