import sys
import os

import numpy as np
from scipy.special import erfc
from scipy.optimize import curve_fit
import matplotlib.pyplot as plt
import csv
from epics import caget, caput, PV

pcdsModulePath = os.path.abspath('/cds/group/pcds/pyps/conda/py39/envs/pcds-5.9.1/lib/python3.9/site-packages/')
sys.path.append(pcdsModulePath)
from pcdsdevices.epics_motor import EpicsMotorInterface, _GetMotorClass
from ophyd.utils import DisconnectedError

from time import sleep, time

# Scope Trace PV: 'SCOP:AS01:SC01:WF_CH2_TRACE'


#### Variable setup and function scope ####

# Where a run writes its csv files.
SAVE_DIR_VAR = 'ERFFIT_DATA_DIR'
DEFAULT_SAVE_DIR = '/sdf/home/u/uedopr/rlemons/opsKnifeScans'

# Minimum time before .DMOV is believed (waiting for the PV to update)
posReadMin = 0.2

# Maximum time to wait for .DMOV (.DMOV never changes if it's at the set point)
posReadMax = 1.0

# Seconds .DMOV must read done continuously before the move is called over.
settleTime = 0.2

# Various errors possible when reading a motor:
#   AttributeError    - If the connected motor is missing the value it is being
#                       asked for (ie. wrong motor class or not pcdsDevice).
#   TypeError         - .get() returned None. Likely disconnection
#   TimeoutError      - The motor just hasn't connected yet
#   DisconnectedError - For some reason the motor got disconnected (prob IOC)
motorErrors = (AttributeError, TypeError, TimeoutError, DisconnectedError)

def saveDir():

	# If one has set the save directory by enviro var, use that. Otherwise 
	# default to ~/rlemons/opsKnifeScans
	fromEnv = os.environ.get(SAVE_DIR_VAR)
	if fromEnv:
		fromEnv = os.path.realpath(os.path.expanduser(os.path.expandvars(fromEnv)))
		if os.path.isdir(fromEnv):
			return fromEnv
		print('{} is set to {!r}, which is not a directory. Using {}'.format(
			SAVE_DIR_VAR, fromEnv, DEFAULT_SAVE_DIR))

	return DEFAULT_SAVE_DIR


#### Class Definition and helper functions ####

def motorClass(pv):

	# Just check the class PV given is a supported pcds EpicsMotor
	cls = _GetMotorClass(pv)

	if not issubclass(cls, EpicsMotorInterface):
		raise TypeError('{!r} is not a PCDS EpicsMotorInterface motor type'.format(pv))

	return cls


def getMotor(pv, name = 'undefined', motorDict = None):

	# One device per PV. motorDict is the master list, carried between scans.
	# The class comes from the PV, so the same PV always resolves the same way.
	if motorDict is None:
		return motorClass(pv)(pv, name = name)

	if pv not in motorDict:
		motorDict[pv] = motorClass(pv)(pv, name = name)

	return motorDict[pv]


def parsePositions(raw, indep):

	if indep is True:
		motorLow, motorSteps, motorHigh = raw.split(':')
		return np.round(np.linspace(float(motorLow),float(motorHigh),int(motorSteps)),3)

	if indep is False:
		return float(raw)

	raise TypeError('indep must be True or False, got {!r}'.format(indep))


class scan:

	def __init__(self, motorList = None, numAvgs = 10, dataPVList = None):

		# .motors holds references to the shared devices. .positions, .indep
		# and .userInput are index matched to it and belong to this scan only,
		# so two scans can drive the same motor with different parameters.

		if motorList is None:
			self.motors = []
		elif isinstance(motorList, list) and all(isinstance(item, EpicsMotorInterface) for item in motorList):
			self.motors = list(motorList)
		else:
			raise TypeError('motorList must be a list of pcdsdevices motor objects')

		if dataPVList is None:
			self.dataPVList = []
		elif isinstance(dataPVList, list) and all(isinstance(item, str) for item in dataPVList):
			self.dataPVList = list(dataPVList)
		else:
			raise TypeError('dataPVList must be a list of PV name strings')

		# A motorList says which motors, not what they do, so the parameters
		# start empty and are filled by updateMotors.
		self.positions = [None] * len(self.motors)
		self.indep = [None] * len(self.motors)
		self.userInput = [None] * len(self.motors)

		self.numAvgs = numAvgs

	def addMotor(self, userInput = None, motorDict = None):

		if userInput is None:
			pv = input('Input the motor PV: ')
			name = input('Enter Motor Name: ')
			indep = input('Is this motor at a fixed position (y/n): ')

			if indep == 'y':
				indep = False
				val = input('What is the motor position: ')
			elif indep == 'n':
				indep = True
				val = input('Enter the motor positions (low:numSteps:high): ')

			userInput = [pv,name,val,indep]

		elif type(userInput) is list and len(userInput) == 4:
			pv = userInput[0]
			name = userInput[1]
			indep = userInput[3]

		else:
			raise TypeError('userInput must be a list of [pv, name, positions, indep]')

		self.motors.append(getMotor(pv,name,motorDict))
		self.positions.append(parsePositions(userInput[2],indep))
		self.indep.append(indep)
		self.userInput.append(userInput)

	def addDataPV(self,dataPV = None):

		if dataPV is None:
			dataPV = input('What is the Data PV: ')
		self.dataPVList.append(dataPV)

	def updateMotors(self, userInputList):

		# Re-stamps every motor in this scan with new parameters, matched to
		# the motors by PV rather than by position in the list.

		if not isinstance(userInputList, list) 	or not all(isinstance(item, list) and len(item) == 4 for item in userInputList):
			raise TypeError('userInputList must be a list of [pv, name, positions, indep] lists')

		if len(userInputList) != len(self.motors):
			raise ValueError('got {} motor inputs for a scan with {} motors'.format(
				len(userInputList), len(self.motors)))

		prefixes = [m.prefix for m in self.motors]

		for ii in range(len(userInputList)):
			userInput = userInputList[ii]
			pv = userInput[0]

			if pv not in prefixes:
				raise ValueError('no motor with PV {!r} in this scan, has {}'.format(
					pv, ', '.join(prefixes)))

			pos = prefixes.index(pv)
			self.positions[pos] = parsePositions(userInput[2], userInput[3])
			self.indep[pos] = userInput[3]
			self.userInput[pos] = list(userInput)

		# One scanning motor per scan.
		###### FIX LATER IF WANTING COMPLEX 2D SCANS ######
		scanning = self.indep.count(True)
		if scanning != 1:
			raise ValueError('a scan needs exactly 1 motor with indep=True, got {}'.format(scanning))


#### Custom built polling of motor movements ####

class ScanAborted(Exception):
	# Custom exception to determine between and aborted scan and other errors
	pass


def arrivalTolerance(motor):

	# Close enough to call it arrived. Checks to see if the motor has a number
	# to try and hit or 0.01 mm (10 um) is the fallback.
	try:
		value = float(motor.retry_deadband.get())
		if value > 0:
			return max(value * 2, 1e-6)
	except motorErrors:
		pass

	return 0.01 # mm (10 um)


def isMoving(motor):

	# True/False from .DMOV, or None if motor has no .DMOV to read.
	try:
		return not bool(motor.motor_done_move.get(use_monitor=False))
	except motorErrors:
		return None


def readPosition(motor):

	# Read position or return None so the polling loop can handle the error
	try:
		return float(motor.position)
	except motorErrors:
		return None


def arrived(motor, target, tolerance = None):

	# Check if the motor has the motor stopped where is supposed to
	#   True  - stopped within tolerance of target
	#   False - still moving
	#   None  - stopped, but NOT at target
	if tolerance is None:
		tolerance = arrivalTolerance(motor)

	if isMoving(motor) is True:
		return False

	position = readPosition(motor)
	if position is None:
		return False

	if abs(position - target) <= tolerance:
		return True

	return None


def moveFailure(status):
	# status is the returned object from motor.mv(), not trusted for when the
	# move is done but will return errors if it fails.

	if status is None:
		return ''

	try:
		if not bool(status.done):
			return ''
		if bool(status.success):
			return ''
	except Exception:
		return ''

	try:
		error = getattr(status, 'exception', None)
		if callable(error):
			error = error()
		if error:
			text = str(error).strip().splitlines()
			return text[0] if text else type(error).__name__
	except Exception:
		pass

	return 'check the motor'


def arrivalTimeout(motor, target, timeout = None, defaultTimeout = None):

	# set timeout programatically based on motor values
	if timeout is not None:
		return float(timeout)

	if defaultTimeout is None:
		defaultTimeout = 20

	position = readPosition(motor)

	# A disconnected motor has no velocity to scale the timeout by.
	try:
		velocity = float(motor.velocity.get())
	except motorErrors:
		return float(defaultTimeout)

	if position is None or not velocity or velocity <= 0:
		return float(defaultTimeout)

	travelTime = abs(target - position) / velocity * 3

	return max(float(defaultTimeout), posReadMin + settleTime + travelTime)


def waitForArrival(motor, target, status = None, shouldAbort = None, timeout = None, poll = None):
	# status is the returned object from motor.mv()

	# Sleep time for while loop
	if poll is None:
		poll = 0.05

	limit = arrivalTimeout(motor, target, timeout)
	tolerance = arrivalTolerance(motor)

	started = time()

	moving = False
	while time()-started < min(posReadMax,limit):
		if shouldAbort is not None and shouldAbort():
			raise ScanAborted('aborted while moving to {}'.format(target))
		mv = isMoving(motor)
		if mv is True or mv is None:
			moving = True
			break
		sleep(poll)

	if not moving:
		position = readPosition(motor)
		if position is not None and abs(position - target) <= tolerance:
			return position
		raise RuntimeError('{} never started moving'.format(motor.prefix))

	settledAt = None
	lastPosition = None
	waitTics = 0

	while True:

		if shouldAbort is not None and shouldAbort():
			raise ScanAborted('aborted while moving to {}'.format(target))

		elapsed = time() - started
		position = readPosition(motor)

		if elapsed > limit:
			reason = moveFailure(status)
			try:
				motor.stop()
			except Exception:
				pass
			raise RuntimeError('{} did not reach {} within {:.1f}s, stopped at {}{}'.format(
				motor.prefix, target, limit, readPosition(motor),
				': ' + reason if reason else ''))

		state = arrived(motor, target, tolerance)
		if state is None and isMoving(motor) is False:
			waitTics += 1
			settledAt = None
			if waitTics >= 3:
				reason = moveFailure(status)
				raise RuntimeError('{} stopped at {} instead of {}{}'.format(
					motor.prefix, position, target,
					': ' + reason if reason else ''))
		elif state is True:
			waitTics = 0
			if settledAt is None:
				settledAt = time()
			elif time() - settledAt >= settleTime:
				return position
		else:
			waitTics = 0
			settledAt = None

		# No .DMOV to read so just have to look at the .RBV value changeing
		# and update the lastPosition and settledAt from that
		if isMoving(motor) is None and position is not None:
			if lastPosition is not None and abs(position - lastPosition) > tolerance:
				settledAt = None
			lastPosition = position

		sleep(poll) # make sure we are not checking too often


#### Motor running and functionality ####

def scanMotorIndex(oneScan):

	# The index in .motors of the one motor this scan sweeps, or None.
	for ii in range(len(oneScan.motors)):
		if oneScan.indep[ii] is True:
			return ii

	return None


def scanMotorName(oneScan, scanNum = 0, askUser = True):

	# The name of the scanning motor, asked for if the config does not have one.
	indepMotor = scanMotorIndex(oneScan)

	name = ''
	if indepMotor is not None:
		if oneScan.userInput[indepMotor]:
			name = oneScan.userInput[indepMotor][1]
		else:
			name = oneScan.motors[indepMotor].name

	if isMissingName(name):
		pv = oneScan.motors[indepMotor].prefix if indepMotor is not None else 'unknown PV'
		if askUser:
			print('Scan {} sweeps {}, which has no name in the config.'.format(
				scanNum + 1, pv))
			name = input('Enter a name for it: ')

		# Reached when askUser is False or when input is blank
		if isMissingName(name):
			name = 'scan{}'.format(scanNum + 1)

	return name


def checkPositions(motor, positions):

	try:
		values = list(positions) # Scanning motor
	except TypeError:
		values = [positions] # Fixed motors

	# Make sure the positions are not out of limit for a motor
	for value in values:
		try:
			motor.check_value(float(value))
		except Exception as e:
			raise ValueError('{} cannot go to {}: {}: {}'.format(
				motor.prefix, value, type(e).__name__, e))

	return True


def readDataPoint(dataPV, numAvgs, shouldAbort = None):

	ydata = np.zeros(numAvgs)
	for ii in range(numAvgs):

		if shouldAbort is not None and shouldAbort():
			raise ScanAborted('aborted while averaging')

		value = dataPV.get()
		if value is None:
			raise RuntimeError('no data from {} (not connected?)'.format(dataPV.pvname))

		ydata[ii] = value
		sleep(0.1)

	return np.average(ydata)


def motorInput(motorPVName=None,motorName='undefined',motorPos=None,dataPVName=None,numAvgs=None,fileName=None,holdPosition=True):

	if motorPVName is None:
		motorPVName = input('Enter the motor PV: ')

	if motorName == 'undefined':
		motorName = input('Enter Motor Name: ')
	
	if motorPos is None:
		motorPos = input('Enter the motor positions (low:numSteps:high): ')
	motorLow, motorStep, motorHigh = motorPos.split(':')
	motorLow = float(motorLow)
	motorStep = int(motorStep)
	motorHigh = float(motorHigh)

	if dataPVName is None:
		dataPVName = input('Enter the data PV: ')

	if numAvgs is None:
		numAvgs = int(input('Number of averages per point: '))
	
	xdataFull = np.round(np.linspace(motorLow,motorHigh,motorStep),3)

	manualMotor = getMotor(motorPVName, name = motorName)
	ydataFull = runMotor(manualMotor, xdataFull, dataPVName)
		
	xdataFull, ydataFull = sortData(xdataFull, ydataFull)
	saveData(xdataFull, ydataFull, fileName)
	plotERF(xdataFull, ydataFull)
	
	return


def runMotor(motor, positions, dataPVName, holdPosition = True, numAvgs = 10,
		shouldAbort = None, report = None):

	# Sweeps one motor through positions, averaging the data PV at each.

	checkPositions(motor, positions)
	dataPV = PV(dataPVName)
	ydataFull = np.zeros(len(positions), dtype = float)

	if holdPosition is True:
		originalPos = readPosition(motor)
	else:
		originalPos = None

	completed = False
	aborted = False
	moveFailed = False

	try:
		for pos in range(len(positions)):

			if shouldAbort is not None and shouldAbort():
				raise ScanAborted('aborted before moving to {}'.format(positions[pos]))
			if report is not None:
				report(pos, len(positions), positions[pos])

			print('Setting position to: ' + str(positions[pos]))

			try:
				status = motor.mv(positions[pos])
				waitForArrival(motor, positions[pos], status, shouldAbort)
			except RuntimeError:
				moveFailed = True
				raise

			ydataFull[pos] = readDataPoint(dataPV, numAvgs, shouldAbort)
			print('Measured Value is: ' + str(ydataFull[pos]))

		completed = True

	except ScanAborted:
		aborted = True
		raise

	finally:
		if not completed:
			try:
				motor.stop()
			except Exception as e:
				print('could not stop {}: {}'.format(motor.prefix, e))

		try:
			dataPV.disconnect()
		except Exception as e:
			print('could not release {}: {}'.format(dataPVName, e))

		# Don't reset positions if a move failed or it was aborted. Could be
		# something bad with the motor or a reason to stop it all.
		if holdPosition is True and originalPos is not None and not aborted and not moveFailed:
			print('------- Resetting Position -------')
			try:
				status = motor.mv(originalPos)
				waitForArrival(motor, originalPos, status)
			except Exception as e:
				print('could not reset {} to {}: {}'.format(
					motor.prefix, originalPos, e))

	return ydataFull


#### Data saving and loading plus formatting ####

def isMissingName(name):

	# 'undefined' is what addMotor and getMotor use when no name was given
	if name is None:
		return True

	name = str(name).strip()
	return not name or name.lower() == 'undefined'


def nameFormat(name):

	# Adds underscores to names for file saving reasons
	# 'Y Motor - OPA Delay' --> 'Y_Motor_-_OPA_Delay'
	token = ''.join(c if (c.isalnum() or c in '.-') else '_' for c in str(name).strip())

	while '__' in token:
		token = token.replace('__', '_')

	return token.strip('_')


def makeSaveNames(scanConfig, baseName = None, askUser = True, directory = None):

	if baseName is None:
		if not askUser:
			raise ValueError('baseName is required when askUser is False')
		baseName = input('Please enter a base filename for this run: ')

	baseName = str(baseName).strip()
	if baseName.endswith('.csv'):
		baseName = baseName[:-4]  # everything assumes you aren't giving filetype
	if not baseName:
		raise ValueError('base filename is empty')

	# If the base name is a valid path, just use that instead
	if directory is None:
		directory = '' if os.path.dirname(baseName) else saveDir()

	names = []
	used = {}

	for scanNum in range(len(scanConfig)):
		motorName = nameFormat(scanMotorName(scanConfig[scanNum], scanNum, askUser))
		if not motorName:
			motorName = 'scan{}'.format(scanNum + 1)

		fileName = '{}_{}'.format(baseName, motorName)

		# If two scans have the same scanning motor, increment the file name
		# for new scans so they don't overwrite.
		used[fileName] = used.get(fileName, 0) + 1
		if used[fileName] > 1:
			fileName = '{}_{}'.format(fileName, used[fileName])

		names.append(os.path.join(directory, fileName + '.csv'))

	summaryName = os.path.join(directory, baseName + '_summary.csv')

	# Check if the file can be written before scanning
	target = os.path.dirname(os.path.abspath(summaryName))
	if not os.path.isdir(target):
		raise IOError('save directory does not exist: {}'.format(target))

	clashes = [n for n in names + [summaryName] if os.path.exists(n)]
	if clashes:
		print('WARNING: these files already exist and will be overwritten:')
		for n in clashes:
			print('    ' + n)

	return names, summaryName


def saveData(xdata,ydata,fileName=None):
	
	xdata, ydata = sortData(xdata, ydata)

	if fileName is None:
		fileName = input('Please enter a filename: ')
		fileName = os.path.join(saveDir(), fileName + '.csv')

	with open(fileName,'w') as f:
		writer = csv.writer(f)
		writer.writerows(zip(xdata,ydata))

	return


# Columns of the run summary for each scan.
SUMMARY_HEADER = ['scan', 'motorName', 'motorPV', 'dataPV', 'numAvgs',
		'points', 'dataFile', 'fitOK', 'y0', 'A', 'w', 'x0',
		'twoW', 'fwhm', 'rSquared', 'note']


def summaryRow(oneScan, ii, scanNum, dataFile, xdata, ydata, fitVals = None, note = ''):

	row = [scanNum + 1,
		scanMotorName(oneScan, scanNum, askUser = False),
		oneScan.motors[ii].prefix,
		oneScan.dataPVList[0] if oneScan.dataPVList else '',
		oneScan.numAvgs,
		len(xdata),
		os.path.basename(dataFile)]

	if fitVals is None:
		# A failed fit still gets a row.
		return row + [False, '', '', '', '', '', '', '', note]

	y0, A, w, x0 = fitVals

	xdata = np.asarray(xdata, dtype = float)
	ydata = np.asarray(ydata, dtype = float)
	fitted = erfFitFunc(xdata, *fitVals)
	ssRes = float(np.sum((ydata - fitted) ** 2))
	ssTot = float(np.sum((ydata - np.mean(ydata)) ** 2))
	rSquared = 1.0 - ssRes / ssTot if ssTot > 0 else ''

	return row + [True, y0, A, w, x0,
		2 * abs(w), np.sqrt(2 * np.log(2)) * abs(w), rSquared, note]


def saveSummary(fileName, rows):

	# Called in a loop as the row is built
	with open(fileName, 'w') as f:
		writer = csv.writer(f)
		writer.writerow(SUMMARY_HEADER)
		writer.writerows(rows)

	return


#### Data analysis, plotting, and manual collection ####

def erfFitFunc(x, y0, A, w, x0):
	return y0 + (A/2) * erfc( (np.sqrt(2)/w) * (x-x0) )


def sortData(xdata,ydata):
	xdata, ydata = (list(t) for t in zip(*sorted(zip(xdata, ydata))))
	return xdata, ydata


def erfFit(xdata,ydata,fitFunc=None,guess=None,bounds=None):

	xdata, ydata = sortData(xdata,ydata)

	xdata = np.array(xdata)
	ydata = np.array(ydata)

	if fitFunc is None:
		fitFunc = erfFitFunc

	if guess is None:
		guess = (np.average(ydata),np.average(ydata),0.2,np.average(xdata))

	if bounds is None:
		bounds = ([-np.inf,-np.inf,-np.inf,-np.inf],[np.inf,np.inf,np.inf,np.inf])

	vals, cov = curve_fit(fitFunc, xdata, ydata, p0=guess, bounds=bounds, maxfev=10000)

	return vals


def plotERF(xdata,ydata,fitVals=None,figNum=None,label=None,doFit=True):

	xdata, ydata = sortData(xdata, ydata)

	# In runConfig the fits are already found so skip refitting and just plot
	if fitVals is None and doFit:
		try:
			fitVals = erfFit(xdata,ydata)
		except (RuntimeError, ValueError, TypeError) as e:
			print('Fit failed: {}'.format(e))
			fitVals = None

	if figNum is None:
		figNum = 1

	xdata = np.array(xdata)
	ydata = np.array(ydata)

	title = 'Manual Knife Edge Scan' if label is None else str(label)

	plt.figure(figNum)
	plt.clf()
	plt.plot(xdata,ydata,'bo',label='data')
	if fitVals is not None:
		plt.plot(xdata,erfFitFunc(xdata, *fitVals),'r-')
		title += ': 2w = ' + str(round(2*abs(fitVals[2]),3)) +', FWHM = ' + str(round(np.sqrt(2*np.log(2))*abs(fitVals[2]),3))
	else:
		title += ': fit failed'
	plt.xlabel('Knife Position (mm)')
	plt.ylabel('Amplitude (arb.)')
	plt.title(title)
	plt.show()

	return


def inputData():

	xdataFull = []
	ydataFull = []

	while True:
		val = input('Enter your x,y pair (q to finish): ')
		if val == 'q':
			xdataFull, ydataFull = sortData(xdataFull,ydataFull)
			saveData(xdataFull,ydataFull)
			plotERF(xdataFull,ydataFull)
			break
		else:
			xdata, ydata = val.split(',')
			xdataFull.append(float(xdata))
			ydataFull.append(float(ydata))
			continue

	return


def plotFile(fileName,figNum=None):

	xdata = []
	ydata = []
	
	with open(fileName,'r') as f:
		reader = csv.reader(f,delimiter=',')
		for row in reader:
			xdata.append(float(row[0]))
			ydata.append(float(row[1]))

	xdata, ydata = sortData(xdata,ydata)
	
	plotERF(xdata,ydata,figNum=figNum)
	
	return


def addData(fileName):
	
	xdataFull = []
	ydataFull = []

	with open(fileName,'r') as f:
		reader = csv.reader(f,delimiter=',')
		for row in reader:
			xdataFull.append(float(row[0]))
			ydataFull.append(float(row[1]))

	while True:
		val = input('Enter your x,y pair (q to finish): ')
		if val == 'q':
			xdataFull, ydataFull = sortData(xdataFull,ydataFull)
			saveData(xdataFull,ydataFull,fileName)
			plotERF(xdataFull,ydataFull)
			break
		else:
			xdata, ydata = val.split(',')
			xdataFull.append(float(xdata))
			ydataFull.append(float(ydata))
			continue

	return


#### Save, load, make configs of motors ####

def makeConfig():

	scanList = []
	motorDict = {}
	numScans = int(input('Number of scans: '))

	for scanNum in range(numScans):
		print('------- Scan '+str(scanNum+1)+' Properties -------')

		scanList.append(scan())

		numMotors = int(input('Number of motors in Scan '+str(scanNum+1)+': '))
		numDataPVs = int(input('Number of Data PVs to read for Scan '+str(scanNum+1)+': '))

		for motorNum in range(numMotors):
			print('------- Scan '+str(scanNum+1)+', Motor '+str(motorNum+1)+' Properties -------')
			# pass motorDict to ensure single instance of each motor
			scanList[scanNum].addMotor(motorDict = motorDict)

		for pvNum in range(numDataPVs):
			print('------- Scan '+str(scanNum+1)+', Data PV '+str(pvNum+1)+' Properties -------')
			scanList[scanNum].addDataPV()

	return scanList


def saveConfig(scanConfig, fileName = None):

	if fileName is None:
		fileName = input('Please enter a filename: ')

	if not fileName.endswith('.txt'):
		fileName += '.txt'

	with open(fileName, 'w') as f:
		numScans = len(scanConfig)
		f.write(str(numScans)+'\n')

		for ii in range(numScans):
			numMotors = len(scanConfig[ii].motors)
			numDataPVs = len(scanConfig[ii].dataPVList)
			numAvgs = scanConfig[ii].numAvgs
			f.write('{},{},{}'.format(numMotors,numDataPVs,numAvgs)+'\n')

			for motor in range(numMotors):
				userInput = scanConfig[ii].userInput[motor]
				f.write('{},{},{},{}'.format(userInput[0],userInput[1],userInput[2],userInput[3])+'\n')

			for dataPV in range(numDataPVs):
				f.write(scanConfig[ii].dataPVList[dataPV]+'\n')

	return


def loadConfig(fileName, motorDict = None):

	# motorDict is a list of devices, keyed by PV. Used by the GUI and to feed
	# succesive loads of configs the already connected devices to avoid too 
	# many PVs being created and connected.
	def clean(x):
		return [x[0].replace("'",""), x[1],  x[2].replace("'",""), x[3] == 'True']

	if motorDict is None:
		motorDict = {}

	with open(fileName, 'r') as f:
		lines = f.readlines()
		lineNum = 0
		config = []

		numScans = int(lines[lineNum].rstrip())
		print('numScans = {}'.format(numScans))
		lineNum += 1

		for ii in range(numScans):
			print('------- Scan {} -------'.format(ii+1))
			config.append(scan())

			numMotors, numDataPVs, numAvgs = map(int,lines[lineNum].rstrip().split(','))
			print('numMotors, numDataPVs, numAvgs = {},{},{}'.format(numMotors,numDataPVs,numAvgs))
			lineNum += 1

			config[ii].numAvgs = numAvgs

			for motor in range(numMotors):
				userInput = clean(lines[lineNum].rstrip().split(','))
				print('Motor {} input = {}'.format(motor+1, userInput))
				lineNum += 1

				# Pass the motorDict to match to already connected motors
				config[ii].addMotor(userInput, motorDict)
	
			for dataPV in range(numDataPVs):
				dPV = lines[lineNum].rstrip()
				print('DataPV {} = {}'.format(dataPV+1, dPV))
				lineNum += 1

				# dataPVs are assumed 'light' and not worth the dict treatment like motors
				config[ii].dataPVList.append(dPV)

			# One scanning motor per scan.
			###### FIX LATER IF WANTING COMPLEX 2D SCANS ######
			scanning = config[ii].indep.count(True)
			if scanning != 1:
				raise ValueError('a scan needs exactly 1 motor with indep=True, got {} in scan {}'.format(scanning, ii))

	return config


#### Main function, run motor configurations from command line or GUI ####

def runConfig(fileName=None, holdPositions=True, saveRun=True, saveNames=None,
		plotRun=True, motorDict=None, baseName=None, summaryName=None,
		askUser=True, shouldAbort=None, report=None):

	# fileName is either a path to a config, or an already built list of scan objects.
	if fileName is None:
		fileName = input('Enter the full path to config file: ')

	if type(fileName) is str:
		scanConfig = loadConfig(fileName, motorDict)
	elif type(fileName) is list and all(isinstance(x, scan) for x in fileName):
		scanConfig = fileName
	else:
		raise TypeError('fileName must be a path to a config file or a list of scan objects')

	summaryRows = []

	# Saving writes one two column csv per scan and a summary of every scan's fit.
	if saveRun is True:
		if saveNames is None:
			saveNames, derivedSummary = makeSaveNames(scanConfig, baseName, askUser)
		else:
			if not isinstance(saveNames, list) or not all(isinstance(item, str) for item in saveNames) or not (len(saveNames)==len(scanConfig)):
				raise TypeError("The save names must be a list of strings matching amount of scans.")
			derivedSummary = os.path.splitext(saveNames[0])[0] + '_summary.csv'

		if summaryName is None:
			summaryName = derivedSummary

	sleep(0.1)  #wait for the motors to initialize, avoids random occasional errors

	# Check all the positions to scan for out of range issues before running
	for scanNum in range(len(scanConfig)):
		for ii in range(len(scanConfig[scanNum].motors)):
			checkPositions(scanConfig[scanNum].motors[ii],scanConfig[scanNum].positions[ii])

	for scanNum in range(len(scanConfig)):

		if shouldAbort is not None and shouldAbort():
			raise ScanAborted('aborted at beginning of scan {}'.format(scanNum + 1))

		print('------- Scan '+str(scanNum+1)+' -------')

		numMotors = len(scanConfig[scanNum].motors)

		originalPos = [readPosition(m) for m in scanConfig[scanNum].motors]

		# Control bools for the finally
		aborted = False
		moveFailed = False

		try:

			print('------- Setting Fixed Motors -------')

			fixedMoves = []
			for ii in range(numMotors):
				if scanConfig[scanNum].indep[ii] is False:
					target = scanConfig[scanNum].positions[ii]
					status = scanConfig[scanNum].motors[ii].mv(target)
					fixedMoves.append((scanConfig[scanNum].motors[ii], target, status))

			try:
				for motor, target, status in fixedMoves:
					waitForArrival(motor, target, status, shouldAbort)
			except RuntimeError:
				# A fixed motor isn't set right
				moveFailed = True
				raise


			print('------- Running Scaning Motor -------')
			for ii in range(numMotors):
				if scanConfig[scanNum].indep[ii] is True:

					scanMotor = ii

					xdataFull = scanConfig[scanNum].positions[scanMotor]

					# holdPositions is False so runConfig resets instead
					try:
						ydataFull = runMotor(
							scanConfig[scanNum].motors[scanMotor],
							xdataFull,
							scanConfig[scanNum].dataPVList[0],
							False,
							scanConfig[scanNum].numAvgs,
							shouldAbort,
							report) # long function call lol
					except RuntimeError:
						moveFailed = True
						raise

					xdataFull, ydataFull = sortData(xdataFull, ydataFull)

					dataFile = ''
					if saveRun is True:
						dataFile = saveNames[scanNum]
						saveData(xdataFull, ydataFull, dataFile)
						print('Saved ' + dataFile)

					# Fit once, so the plot and the summary are the same
					fitVals = None
					note = ''
					try:
						fitVals = erfFit(xdataFull, ydataFull)
					except (RuntimeError, ValueError, TypeError) as e:
						note = str(e)
						print('Fit failed for scan {}: {}'.format(scanNum+1, note))

					if saveRun is True:
						summaryRows.append(summaryRow(scanConfig[scanNum], scanMotor,
							scanNum, dataFile, xdataFull, ydataFull, fitVals, note))

						# Build summary as it goes and capture failed fits
						saveSummary(summaryName, summaryRows)

					if plotRun is True:
						plotERF(xdataFull, ydataFull, fitVals, figNum=scanNum+1,
							label=scanMotorName(scanConfig[scanNum], scanNum, askUser=False),
							doFit=False)

		except ScanAborted:
			aborted = True
			raise

		finally:
			if holdPositions is True and not aborted and not moveFailed:
				print('------- Resetting positions -------')

				resetMoves = []
				for ii in range(numMotors):
					if originalPos[ii] is None:
						print('no starting position read for {}, not resetting'.format(
							scanConfig[scanNum].motors[ii].prefix))
						continue
					try:
						status = scanConfig[scanNum].motors[ii].mv(originalPos[ii])
						resetMoves.append((scanConfig[scanNum].motors[ii],
							originalPos[ii], status))
					except Exception as e:
						print('could not reset {}: {}'.format(
							scanConfig[scanNum].motors[ii].prefix, e))

				for motor, target, status in resetMoves:
					try:
						waitForArrival(motor, target, status)
					except Exception as e:
						print('reset of {} did not finish: {}'.format(motor.prefix, e))

	if saveRun is True and summaryRows:
		print('Saved summary ' + summaryName)

	return
