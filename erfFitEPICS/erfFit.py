import sys
import os

import numpy as np
from scipy.special import erfc
from scipy.optimize import curve_fit
import matplotlib.pyplot as plt
import csv
from epics import caget, caput, PV

#pcdsModulePath = os.path.abspath('/cds/group/pcds/pyps/conda/py39/envs/pcds-5.9.1/lib/python3.9/site-packages/')
#sys.path.append(pcdsModulePath)
#from pcdsdevices.epics_motor import Newport

from time import sleep, time

# Scope Trace PV: 'SCOP:AS01:SC01:WF_CH2_TRACE'

class motor:

	def __init__(self, pv, name = 'undefined', positions = None, indep = None, userInput = None):
		self.pv = pv
		self.putPV = pv + '.VAL'
		self.getPV = pv + '.RBV'
		self.name = name
		if positions is None:
			self.positions = []
		else:
			self.positions = positions
		self.indep = indep
		self.userInput = userInput

	def position(self):
		return caget(self.getPV)

	def move(self, target, tolerance = 0.01, timeout = 60.0, settle = 0.2, poll = 0.1):

		target = float(target)
		caput(self.putPV, target)

		started = time()
		settledAt = None

		while True:
			position = self.position()

			if position is None:
				raise RuntimeError('{} is not answering'.format(self.getPV))

			if abs(target - position) <= tolerance:
				if settledAt is None:
					settledAt = time()
				elif time() - settledAt >= settle:
					return position
			else:
				settledAt = None

			if time() - started > timeout:
				raise RuntimeError('{} did not reach {} within {:.0f}s, stopped at {}'.format(
					self.pv, target, timeout, position))

			sleep(poll)
		

class scan:

	def __init__(self, motorList = None, numAvgs = 10, dataPVList = None):
		
		if motorList is None:
			self.motors = []
		else:
			self.motors = list(motorList)

		if dataPVList is None:
			self.dataPVList = []
		else:
			self.dataPVList = list(dataPVList)

		self.numAvgs = numAvgs

	def addMotor(self, userInput = None):

		if userInput == None:
			pv = input('Input the motor PV: ' )
			name = input('Enter Motor Name: ')
			indep = input('Is this motor at a fixed position (y/n): ')

			if indep == 'y':
				indep = False
				val = input('What is the motor position: ')
				positions = float(val)
			elif indep == 'n':
				indep = True
				val = input('Enter the motor positions (low:numSteps:high): ')
				motorLow, motorSteps, motorHigh = val.split(':')
				motorLow = float(motorLow)
				motorHigh = float(motorHigh)
				motorSteps = int(motorSteps)
				positions = np.round(np.linspace(motorLow,motorHigh,motorSteps),3)

			userInput = [pv,name,val,indep]

		elif type(userInput) is list and len(userInput) == 4:
			pv = userInput[0]
			name = userInput[1]
			indep = userInput[3]
			
			if indep is True:		
				motorLow, motorSteps, motorHigh = userInput[2].split(':')
				motorLow = float(motorLow)
				motorHigh = float(motorHigh)
				motorSteps = int(motorSteps)
				positions = np.round(np.linspace(motorLow,motorHigh,motorSteps),3)
			elif indep is False:
				positions = float(userInput[2])

		self.motors.append(motor(pv,name,positions,indep,userInput))

	def addDataPV(self,dataPV = None):

		if dataPV == None:
			dataPV = input('What is the Data PV: ')
		self.dataPVList.append(dataPV)


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


def runMotor(motor, dataPVName, holdPosition = True, numAvgs = 10):

	dataPV = PV(dataPVName)
	ydata = np.zeros(numAvgs)
	ydataFull = np.zeros(len(motor.positions), dtype = float)

	if holdPosition == True:
		originalPos = motor.position()

	for pos in range(len(motor.positions)):
		print('Setting position to: ' + str(motor.positions[pos]))
		motor.move(motor.positions[pos])
		
		for ii in range(numAvgs):
			value = dataPV.get()
			if value is None:
				raise RuntimeError('no data from {} (not connected?)'.format(dataPVName))
			ydata[ii] = value
			sleep(0.1)
	
		ydataFull[pos] = np.average(ydata)
		print('Measured Value is: ' + str(ydataFull[pos]))

	if holdPosition == True:
		print('------- Resetting Position -------')
		motor.move(originalPos)

	return ydataFull


def saveData(xdata,ydata,fileName=None):
	
	xdata, ydata = sortData(xdata, ydata)

	if fileName is None:
		fileName = input('Please enter a filename: ')
		fileName = '/sdf/home/u/uedopr/rlemons/opsKnifeScans/'+ fileName + '.csv'

	with open(fileName,'w') as f:
		writer = csv.writer(f)
		writer.writerows(zip(xdata,ydata))
	
	return


def plotERF(xdata,ydata,fitVals=None,figNum=None):
	
	xdata, ydata = sortData(xdata, ydata)

	if fitVals is None:
		fitVals = erfFit(xdata,ydata)

	if figNum is None:
		figNum = 1

	xdata = np.array(xdata)
	ydata = np.array(ydata)

	plt.figure(figNum)
	plt.clf()
	plt.plot(xdata,ydata,'bo',label='data')
	plt.plot(xdata,erfFitFunc(xdata, *fitVals),'r-')
	plt.xlabel('Knife Postion (mm)')
	plt.ylabel('Amplitude (arb.)')
	plt.title('Manual Knife Edge Scan: 2w = ' + str(round(2*abs(fitVals[2]),3)) +', FWHM = ' + str(round(np.sqrt(2*np.log(2))*abs(fitVals[2]),3)))
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

	manualMotor = motor(motorPVName, motorName, xdataFull, True)
	ydataFull = runMotor(manualMotor, dataPVName, holdPosition, numAvgs)
		
	xdataFull, ydataFull = sortData(xdataFull, ydataFull)
	saveData(xdataFull, ydataFull, fileName)
	plotERF(xdataFull, ydataFull)
	
	return


def makeConfig():

	scanList = []
	numScans = int(input('Number of scans: '))
	
	for scanNum in range(numScans):
		print('------- Scan '+str(scanNum+1)+' Properties -------')

		scanList.append(scan())

		numMotors = int(input('Number of motors in Scan '+str(scanNum+1)+': '))
		numDataPVs = int(input('Number of Data PVs to read for Scan '+str(scanNum+1)+': '))
		
		for motorNum in range(numMotors):
			print('------- Scan '+str(scanNum+1)+', Motor '+str(motorNum+1)+' Properties -------')
			scanList[scanNum].addMotor()

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
				userInput = scanConfig[ii].motors[motor].userInput
				f.write('{},{},{},{}'.format(userInput[0],userInput[1],userInput[2],userInput[3])+'\n')

			for dataPV in range(numDataPVs):
				f.write(scanConfig[ii].dataPVList[dataPV]+'\n')

	return


def loadConfig(fileName):

	def clean(x):
		return [x[0].replace("'",""), x[1],  x[2].replace("'",""), x[3] == 'True']

	if fileName.endswith('.txt'):
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

					config[ii].addMotor(userInput)
		
				for dataPV in range(numDataPVs):
					dPV = lines[lineNum].rstrip()
					print('DataPV {} = {}'.format(dataPV+1, dPV))
					lineNum += 1

					config[ii].dataPVList.append(dPV)

	else:
		raise TypeError('File must be a .txt')

	return config


def runConfig(fileName=None, holdPositions=True):

	if fileName is None:
		fileName = input('Enter the full path to config file: ')

	if type(fileName) is str:
		scanConfig = loadConfig(fileName)
	elif type(fileName) is list and all(isinstance(x, scan) for x in fileName):
		scanConfig = fileName
	else:
		raise TypeError('fileName must be a path to a config file or a list of scan objects')
	
	sleep(0.1) #wait for the motors to initialize, avoids random occasional errors
	
	for scanNum in range(len(scanConfig)):
		
		print('------- Scan '+str(scanNum+1)+' -------')

		numMotors = len(scanConfig[scanNum].motors)
		originalPos = np.zeros(numMotors)

		print('------- Setting Fixed Motors -------')
		for ii in range(numMotors):
			
			originalPos[ii] = scanConfig[scanNum].motors[ii].position()

			if scanConfig[scanNum].motors[ii].indep == False:
				scanConfig[scanNum].motors[ii].move(scanConfig[scanNum].motors[ii].positions)

		print('------- Running Scaning Motor -------')
		for ii in range(numMotors):
	
			if scanConfig[scanNum].motors[ii].indep == True:
				
				xdataFull = scanConfig[scanNum].motors[ii].positions
				ydataFull = runMotor(scanConfig[scanNum].motors[ii],scanConfig[scanNum].dataPVList[0],False,scanConfig[scanNum].numAvgs)
				
				xdataFull, ydataFull = sortData(xdataFull, ydataFull)
				saveData(xdataFull, ydataFull)
				plotERF(xdataFull, ydataFull,figNum=scanNum+1)

		if holdPositions == True:
			print('------- Resetting positions -------')
			for ii in range(numMotors):
				if originalPos[ii] is None or np.isnan(originalPos[ii]):
					print('no starting position read for {}, not resetting'.format(
						scanConfig[scanNum].motors[ii].pv))
					continue
				scanConfig[scanNum].motors[ii].move(originalPos[ii])

	return
