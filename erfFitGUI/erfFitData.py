# Knife edge data loading and erf fitting, standalone
# Randy Lemons

# Imports numpy and scipy only. No epics, no pcdsdevices, no Qt, so this can be
# used headless and does not pull in anything that talks to hardware.
#
# The fit function, the initial guess and the spot size formulas are kept
# identical to erfFit.py (erfFitFunc, erfFit, plotERF) so that numbers shown in
# the GUI match what the command line module reports for the same data.

import csv
import os

import numpy as np
from scipy.special import erfc
from scipy.optimize import curve_fit


def erfFitFunc(x, y0, A, w, x0):
	return y0 + (A / 2) * erfc((np.sqrt(2) / w) * (x - x0))


def sortData(xdata, ydata):
	xdata, ydata = (list(t) for t in zip(*sorted(zip(xdata, ydata))))
	return xdata, ydata


class FitResult:

	# One knife edge data set, its fit, and everything the plot window needs.

	def __init__(self, label=''):
		self.label = label
		self.path = ''
		self.x = np.array([])
		self.y = np.array([])
		self.fitX = np.array([])
		self.fitY = np.array([])
		self.y0 = None
		self.amplitude = None
		self.w = None
		self.x0 = None
		self.errors = None
		self.rSquared = None
		self.fitOK = False
		self.error = None

	def twoW(self):
		if self.w is None:
			return None
		return 2 * abs(self.w)

	def fwhm(self):
		# Same expression as erfFit.plotERF: sqrt(2 ln2) * w. For an intensity
		# profile exp(-2 r^2 / w^2), w is the 1/e^2 radius and this is the full
		# width at half maximum.
		if self.w is None:
			return None
		return np.sqrt(2 * np.log(2)) * abs(self.w)

	def parameterRows(self):
		# (name, value) pairs for the parameter table. The motor units are mm, so
		# every width derived from w is in mm and the name says so: the table
		# shows a bare number and is the only place these are labelled.
		rows = []
		if not self.fitOK:
			rows.append(('fit', 'failed'))
			if self.error:
				rows.append(('reason', self.error))
			rows.append(('points', '{}'.format(len(self.x))))
			return rows

		rows.append(('2w (spot size) (mm)', fmtValue(self.twoW())))
		rows.append(('FWHM (mm)', fmtValue(self.fwhm())))
		rows.append(('w (1/e^2 radius) (mm)', fmtValue(self.w)))
		rows.append(('x0 (edge centre)', fmtValue(self.x0)))
		rows.append(('y0 (offset)', fmtValue(self.y0)))
		rows.append(('A (amplitude)', fmtValue(self.amplitude)))
		rows.append(('R squared', fmtValue(self.rSquared)))
		rows.append(('points', '{}'.format(len(self.x))))
		return rows


def fmtValue(val):
	# Three decimals and nothing else. The fit uncertainties are deliberately
	# not shown: a knife edge scan is read for the spot size, and "0.091 +/-
	# 0.0023" doubled the width of every row to say something nobody acts on.
	# self.errors is still kept on the result for anyone who wants it.
	if val is None:
		return ''
	if not np.isfinite(val):
		return ''
	# Scientific below 1e-3 and at 1e5, where fixed point reads 0.000 or runs
	# off the column. Still three decimals, so the rule holds either way.
	if val != 0 and not (1e-3 <= abs(val) < 1e5):
		return '{:.3e}'.format(val)
	return '{:.3f}'.format(val)


def loadScanFile(path):
	# Reads the two column csv that erfFit.saveData writes. Returns
	# (x, y, error). Never raises.
	x = []
	y = []

	if not path:
		return None, None, 'No file given'
	if not os.path.isfile(path):
		return None, None, 'File not found: {}'.format(path)

	try:
		with open(path, 'r') as f:
			for lineNum, row in enumerate(csv.reader(f)):
				if not row or not row[0].strip():
					continue
				if len(row) < 2:
					return None, None, 'Line {} does not have two columns'.format(lineNum + 1)
				try:
					x.append(float(row[0]))
					y.append(float(row[1]))
				except ValueError:
					# A header line at the top is fine, anything else is not.
					if lineNum == 0:
						continue
					return None, None, 'Line {} is not a pair of numbers'.format(lineNum + 1)
	except IOError as e:
		return None, None, 'Could not read file: {}'.format(e)

	if len(x) < 2:
		return None, None, 'Need at least 2 points, found {}'.format(len(x))

	x, y = sortData(x, y)
	return np.array(x), np.array(y), None


def fitScan(x, y, label='', path=''):
	# Fits one data set and returns a FitResult. A failed fit is reported in the
	# result rather than raised, so the raw data can still be plotted.
	result = FitResult(label)
	result.path = path
	result.x = np.asarray(x, dtype=float)
	result.y = np.asarray(y, dtype=float)

	if len(result.x) < 4:
		result.error = 'need at least 4 points to fit 4 parameters, have {}'.format(len(result.x))
		return result

	# Same starting guess as erfFit.erfFit.
	guess = (np.average(result.y), np.average(result.y), 0.2, np.average(result.x))

	try:
		vals, cov = curve_fit(erfFitFunc, result.x, result.y, p0=guess, maxfev=10000)
	except (RuntimeError, ValueError, TypeError) as e:
		result.error = str(e)
		return result

	result.y0, result.amplitude, result.w, result.x0 = vals

	with np.errstate(invalid='ignore'):
		diag = np.diag(cov)
		result.errors = np.sqrt(np.where(diag >= 0, diag, np.nan))

	fitted = erfFitFunc(result.x, *vals)
	ssRes = float(np.sum((result.y - fitted) ** 2))
	ssTot = float(np.sum((result.y - np.mean(result.y)) ** 2))
	result.rSquared = 1.0 - ssRes / ssTot if ssTot > 0 else None

	# Dense curve for drawing.
	result.fitX = np.linspace(result.x.min(), result.x.max(), 300)
	result.fitY = erfFitFunc(result.fitX, *vals)

	result.fitOK = True
	return result


def loadAndFit(path, label=None):
	# Convenience wrapper: csv file in, FitResult out.
	if label is None:
		label = os.path.splitext(os.path.basename(path))[0]

	x, y, error = loadScanFile(path)
	if error:
		result = FitResult(label)
		result.path = path
		result.error = error
		return result

	return fitScan(x, y, label, path)


if __name__ == '__main__':
	import sys

	if len(sys.argv) > 1:
		paths = sys.argv[1:]
	else:
		# No arguments: fit every csv in the scan data directory.
		import erfFitPaths
		here = erfFitPaths.dataDir()
		print('Reading scan data from {}\n'.format(here))
		paths = sorted([os.path.join(here, f) for f in os.listdir(here) if f.endswith('.csv')])
		if not paths:
			print('No csv files found. Pass filenames explicitly, or set {}.'.format(
				erfFitPaths.DATA_DIR_VAR))

	for path in paths:
		result = loadAndFit(path)
		print('{}  ({})'.format(result.label, 'fit ok' if result.fitOK else 'FIT FAILED'))
		for name, value in result.parameterRows():
			print('    {:<22} {}'.format(name, value))
		print('')
