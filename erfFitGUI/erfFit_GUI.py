#!/cds/sw/ds/ana/conda2/inst/envs/ps-4.6.3/bin/python
# Viewer GUI for erfFit knife edge scan configs
# Randy Lemons

# The shebang above is the UED environment, matching the other scripts in this
# folder. The uedopr account does not set an environment in its shell startup,
# so the interpreter has to be named here.
#
# To run somewhere else without editing this line, call the interpreter
# directly, for example on S3DF:
#	/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps-4.6.3/bin/python erfFit_GUI.py
#
# Directories for configs and scan data are not hard coded. See erfFitPaths.py,
# or set ERFFIT_CONFIG_DIR and ERFFIT_DATA_DIR.

# Shows a live camera image, the motor PV, name and scan range of every planned
# run in a config file, and per motor jog controls once the motors are connected.
# Double clicking a motor or data PV in the scan list edits it; changed values
# are marked in blue and can be saved back out to a config file.
#
# The motor jog controls are live: the absolute box and the plus and minus
# buttons do move motors. So does Run scan, which drives erfFit.runConfig on a
# ScanWorker thread. Everything else reads only. The places that write are
# MotorRow.startMove(), MotorRow.onStop(), and the scan itself.
#
# Jogging is locked out while a scan runs, because the scan drives the same
# device objects the jog rows poll and a jog would move a motor out from under
# the sweep. Stop all stays live throughout.
#
# Moves go through the Newport device the scans already use, built by
# erfFit.getMotor, by way of its mv() and mvr() methods. Those are
# non-blocking, unlike move(), whose wait argument defaults to True and would
# freeze the window for the whole travel. Progress is polled from the shared
# timer.
#
# Note this module does NOT import erfFit, on purpose. erfFit.loadConfig() calls
# scan.addMotor(), which builds Newport device objects, so just loading a config
# through erfFit would connect to live EPICS motors. Config parsing lives in
# erfFitConfig.py, which imports nothing but os, so opening a file in this GUI
# never touches hardware. Connecting is a separate, explicit button.

import sys
import os
import time

import numpy as np
import pyqtgraph as pg
from epics import PV

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QDoubleValidator, QFont, QIntValidator,
	QPainterPath)
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
	QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
	QInputDialog,
	QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
	QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
	QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import erfFitConfig
import erfFitData
import erfFitPaths

# Must be set before any ImageView is built. Without it pyqtgraph treats the
# first array axis as x, so an (height,width) image comes out transposed, which
# would quietly swap the X and Y motor sense of a knife edge image.
pg.setConfigOptions(imageAxisOrder='row-major')

# Cameras in the UED space. Add one by adding a line. The one named in
# DEFAULT_CAMERA is selected at startup.
CAMERAS = [
	{'label': 'Sample 30 Deg',		'prefix': 'UED:GIGE:02:IMAGE1:'},
	{'label': 'Sample Front',		'prefix': 'UED:GIGE:04:IMAGE1:'},
	{'label': 'VCC',				'prefix': 'UED:GIGE:06:IMAGE1:'},
]

DEFAULT_CAMERA = 'Sample 30 Deg'

# Camera poll interval, in ms. This was a Rate dropdown with 2, 5 and 10 Hz in
# it, and 5 Hz is the only one anybody ever used, so the box became the
# Orientation one below and the rate is now this constant. Everything that
# needs the tick length reads the timer, not this, so changing it here is
# enough.
CAMERA_INTERVAL = 200

# Display orientations, as (label, mirror, rotation). The eight are the whole
# set of ways a square grid can be put back on itself: a horizontal mirror or
# not, times a quarter turn count. Anything else is one of these eight by
# another name - a vertical mirror is 'Mirror + 180', and a transpose is
# 'Mirror + 90 CW'.
#
# The mirror is applied FIRST and then the rotation, which is what the labels
# read as left to right and what orientImage and orientPoint both do. The order
# matters: mirroring after a 90 turn gives a different picture.
#
# DISPLAY ONLY. This turns the pixels on screen and nothing else. The frame that
# goes nowhere near here is the one a scan measures: erfFit reads a data PV, not
# this image, so no fit result and no motor direction changes when this does.
# What it is for is making the view match how the camera is mounted, or match the
# orientation somebody is used to on another screen.
ORIENTATIONS = [
	('None',				False,	0),
	('Rotate 90 CW',		False,	90),
	('Rotate 180',			False,	180),
	('Rotate 270 CW',		False,	270),
	('Mirror',				True,	0),
	('Mirror + 90 CW',		True,	90),
	('Mirror + 180',		True,	180),
	('Mirror + 270 CW',		True,	270),
]

# How much one notch of the mouse wheel zooms the image, as a divisor on
# pyqtgraph's own rate. Its default moves 34.6% per notch, which overshoots the
# spot you are trying to look at on a camera frame; a third of that is 10.4%.
#
# A divisor rather than a replacement number because pyqtgraph does not zoom by
# a factor per notch, it does 1.02 ** (delta * wheelScaleFactor). Dividing that
# factor divides the exponent, so three notches here cover exactly what one
# notch covered before and nothing else about the behaviour changes.
WHEEL_ZOOM_DIVISOR = 3.0

# The markers over the image are the PCDS CamViewer crosses, which live on the
# CAMERA BASE and not under the image plugin: UED:GIGE:04:Cross1X while the
# frame comes from UED:GIGE:04:IMAGE1:. cameraBase() does that prefix surgery.
#
# Not the areaDetector NDOverlay plugin. That was the first guess and it is
# wrong: Over1/CenterX_RBV has no hits anywhere in the UED tree, while these do,
# with recorded values, in /sdf/home/u/uedopr/fuhaoji/UEDagentic/codes/
# (PVnamelist_GUED.py 'eBeam cross positions', and results_PVtest_run7.txt
# where UED:GIGE:03:Cross2X reads 1080).
#
# MARKER_SLOTS is how many crosses to look for. CamViewer has four per camera;
# UED's own PV lists only ever name Cross2, the eBeam cross position, so the
# other three are read on the chance they are set and cost two disconnected PVs
# each when they are not.
MARKER_CROSS = 'Cross'
MARKER_SLOTS = 4
MARKER_FIRST_SLOT = 1

# Per PV read budget, and a cap on the whole marker read. These share a camera
# tick with the image and the motor rows, so both are far below the 200 ms the
# image PVs use. See the note in MarkerFeed.readMarkers.
MARKER_TIMEOUT = 0.02
MARKER_BUDGET = 0.05

# CamViewer's marker colours, in its own order: 1 blue, 2 red, 3 cyan, 4
# magenta. Matching them is the point - somebody reading a position off the
# CamViewer screen and looking at this window should not have to work out which
# cross is which. A cross beyond the fourth, if a camera ever has one, cycles.
MARKER_COLORS = ['#0000ff', '#ff0000', '#00ffff', '#ff00ff']

# Pixels across the whole cross, and the pen width in pixels. Thin and plain,
# like CamViewer draws it. No halo and no label: the cross covers whatever is
# under it, which is accepted, and the number beside it was noise once there
# are four of them.
MARKER_SIZE = 24
MARKER_WIDTH = 1


def markerSymbol():
	# Two bare lines, which is not what pyqtgraph's '+' is: that one is the
	# outline of a plus shaped polygon, so the pen traces a 13 segment border
	# and the result reads as a thick hollow cross however narrow the pen. This
	# draws one horizontal and one vertical stroke and nothing else.
	#
	# Normalised to -0.5..0.5, the range pyqtgraph scales by `size`, so the
	# cross is MARKER_SIZE pixels across.
	path = QPainterPath()
	path.moveTo(-0.5, 0.0)
	path.lineTo(0.5, 0.0)
	path.moveTo(0.0, -0.5)
	path.lineTo(0.0, 0.5)
	return path

COLOR_SCANNING = QColor(205, 240, 205)
COLOR_WARNING = QColor(255, 232, 190)
COLOR_HEADER = QColor(228, 228, 228)
COLOR_SYNTH = QColor(110, 110, 110)
COLOR_EDITED = QColor(215, 232, 255)
COLOR_EDITED_TEXT = QColor(20, 70, 150)

# The blue 'edited' badge says how much changed. This one sits beside it and
# says the file on disk does not have those changes, which matters more once a
# scan can be run: a run uses what is on screen, not what was loaded.
STYLE_UNSAVED = ('color: #8c2f39; background-color: #fbdcdc; '
	'border: 1px solid #e3a9a9; padding: 2px 6px;')


def cameraBase(prefix):
	# 'UED:GIGE:04:IMAGE1:' -> 'UED:GIGE:04:'. The cross PVs and the ROI plugin
	# both hang off the camera base, a level above the image plugin. A prefix
	# that does not end in an image plugin is returned unchanged, which is what
	# makes this safe for a camera added later with a different layout.
	parts = [p for p in prefix.split(':') if p]
	if parts and parts[-1].upper().startswith('IMAGE'):
		parts = parts[:-1]
	return ':'.join(parts) + ':' if parts else ''


class MarkerFeed:

	# The CamViewer cross positions for one camera, plus the ROI origin needed
	# to put them in the right place. Separate from CameraFeed's image PVs
	# because markers are allowed to be absent: a camera nobody has set a cross
	# on leaves these disconnected for ever and the image must still show.
	#
	# Same lifetime rule as CameraFeed. Built once per camera and kept, because
	# PV() does not deduplicate.

	def __init__(self, prefix):
		self.base = cameraBase(prefix)
		self.slots = []
		for n in range(MARKER_FIRST_SLOT, MARKER_FIRST_SLOT + MARKER_SLOTS):
			stem = '{}{}{}'.format(self.base, MARKER_CROSS, n)
			# auto_monitor left on, unlike the image PVs. These are two small
			# scalars per cross that change only when somebody drags the marker,
			# so a monitor makes the per tick read a cached lookup instead of a
			# channel access round trip at the camera rate. The image array is
			# the opposite case, which is why it sets auto_monitor=False.
			self.slots.append({
				'number': n,
				'x': PV(stem + 'X'),
				'y': PV(stem + 'Y'),
			})

		# The crosses are in full sensor pixels, but the frame on screen is
		# whatever the ROI plugin cropped out of it, with (0,0) at the ROI
		# corner. Without this subtraction a cross on a cropped camera lands at
		# the wrong place by the ROI origin, and UED:GIGE:06 really is cropped:
		# a recorded caget has its ROI1:MinX at 281, MinY at 295.
		self.roiX = PV(self.base + 'ROI1:MinX')
		self.roiY = PV(self.base + 'ROI1:MinY')

	def roiOrigin(self):
		# (x, y) of the displayed frame's top left in sensor pixels. Zero when
		# there is no ROI plugin, which is the right answer for an uncropped
		# camera and the safe one for a camera whose layout we cannot read.
		origin = []
		for pv in (self.roiX, self.roiY):
			value = pv.get(timeout=MARKER_TIMEOUT) if pv.connected else None
			try:
				origin.append(float(value) if value is not None else 0.0)
			except (TypeError, ValueError):
				origin.append(0.0)
		return origin[0], origin[1]

	def readMarkers(self):
		# Returns a list of (x, y, label) in DISPLAYED image pixels for the
		# crosses that are set. Never raises, and bounded: this runs on the same
		# QTimer that reads the image, so it must not be able to outlast a tick.
		#
		# The timeouts are deliberately tiny. A disconnected PV is skipped on
		# the connected check and costs nothing, but a PV that is connected to
		# an IOC which has stopped answering burns its whole timeout, and ten of
		# those at the 200 ms the image uses would freeze the window for seconds
		# per tick. A marker is decoration: if it cannot be read instantly it is
		# worth skipping until the next tick, which is 200 ms away.
		#
		# Per PV timeouts alone are not enough, because they multiply by the
		# number of reads. MARKER_BUDGET caps the lot: once it is spent the
		# remaining crosses are left for the next tick, so a sick IOC costs a
		# partial overlay rather than a frozen window.
		deadline = time.time() + MARKER_BUDGET

		originX, originY = self.roiOrigin()

		markers = []
		for slot in self.slots:
			if time.time() > deadline:
				break

			if not slot['x'].connected or not slot['y'].connected:
				continue

			# No use_monitor=False here, so these come from the monitor cache
			# and the timeout is only reached if the monitor has never fired.
			x = slot['x'].get(timeout=MARKER_TIMEOUT)
			y = slot['y'].get(timeout=MARKER_TIMEOUT)
			if x is None or y is None:
				continue

			try:
				x = float(x)
				y = float(y)
			except (TypeError, ValueError):
				# A waveform or an enum where a number was expected. Skip the
				# marker rather than take the camera view down with it.
				continue

			# There is no enable flag for these: CamViewer keeps that in its own
			# state, not in EPICS. An unset cross reads 0,0, so that is taken as
			# "not placed". It costs the top left pixel as a marker position,
			# which is not a place anybody puts a reference cross.
			if x == 0.0 and y == 0.0:
				continue

			markers.append((x - originX, y - originY, slot['number']))
		return markers


class CameraFeed:

	# All EPICS contact for one camera prefix. Built once per camera and kept
	# for the life of the application, because PV() does not deduplicate and
	# rebuilding on every camera switch would leak channel access channels.

	def __init__(self, prefix):
		self.prefix = prefix
		self.dataPV = PV(prefix + 'ArrayData', auto_monitor=False)
		# Three size PVs, not two. On a colour camera ArraySize0_RBV is the
		# number of colour channels and the picture dimensions move up to
		# ArraySize1/2, so reading only the first two gets the shape wrong.
		self.sizePVs = [
			PV(prefix + 'ArraySize0_RBV', auto_monitor=False),
			PV(prefix + 'ArraySize1_RBV', auto_monitor=False),
			PV(prefix + 'ArraySize2_RBV', auto_monitor=False),
		]
		self.colorModePV = PV(prefix + 'ColorMode_RBV', auto_monitor=False)

	def isConnected(self):
		return bool(self.dataPV.connected)

	def readImage(self):
		# Returns (image, statusText) on success, or (None, reason). The
		# connected check comes first and costs almost nothing, which is what
		# lets this run at full rate on a machine with no EPICS at all.
		if not self.dataPV.connected:
			return None, 'no connection - EPICS unreachable?'

		data = self.dataPV.get(timeout=0.2, use_monitor=False)
		if data is None:
			return None, 'connected, but no data returned'

		data = np.asarray(data)
		if data.size == 0:
			return None, 'connected, but the array is empty'

		dims = [pv.get(timeout=0.2) if pv.connected else None for pv in self.sizePVs]

		# ColorMode_RBV may come back as an index or as a string, depending on
		# whether the PV was created with as_string. Both are handled.
		colorMode = self.colorModePV.get(timeout=0.2) if self.colorModePV.connected else None

		img, note = reshapeImage(data, dims, colorMode)
		if img is None:
			return None, note

		if img.ndim == 3:
			shapeText = '{} x {} colour'.format(img.shape[1], img.shape[0])
		else:
			shapeText = '{} x {}'.format(img.shape[1], img.shape[0])

		status = '{} | {} | min {:g} max {:g}'.format(
			shapeText, data.dtype, img.min(), img.max())
		if note:
			status += ' | ' + note
		return img, status


# areaDetector NDColorMode values. Mono and RGB1 are the two seen on the UED
# cameras, but all three RGB layouts are cheap to support and getting one wrong
# produces a stripey mess rather than an error, so they are all handled.
COLOR_MODE_MONO = 0
COLOR_MODE_BAYER = 1
COLOR_MODE_RGB1 = 2
COLOR_MODE_RGB2 = 3
COLOR_MODE_RGB3 = 4

COLOR_MODE_NAMES = {
	COLOR_MODE_MONO: 'Mono',
	COLOR_MODE_BAYER: 'Bayer',
	COLOR_MODE_RGB1: 'RGB1',
	COLOR_MODE_RGB2: 'RGB2',
	COLOR_MODE_RGB3: 'RGB3',
}


def normalizeColorMode(colorMode):
	# Accepts an index, a numpy scalar, or a string like 'RGB1'.
	if colorMode is None:
		return None
	if isinstance(colorMode, str):
		text = colorMode.strip().lower()
		for value, name in COLOR_MODE_NAMES.items():
			if text == name.lower():
				return value
		try:
			return int(text)
		except ValueError:
			return None
	try:
		return int(colorMode)
	except (TypeError, ValueError):
		return None


def reshapeImage(data, dims, colorMode=None):
	# Turns the flat ArrayData waveform into a 2D mono image or a 3D (height,
	# width, 3) colour image.
	#
	# Two things make this more than a plain reshape.
	#
	# First, areaDetector keeps ArrayData allocated at the maximum frame size,
	# so with binning or a smaller ROI the waveform is LONGER than the product
	# of the dimensions and the tail is stale. The data has to be sliced to the
	# expected length first. The older scripts use np.array_split(data, height),
	# which both ignores width and mishandles this.
	#
	# Second, ArraySize0_RBV is the FASTEST varying dimension, not the width.
	# On a colour camera in RGB1 the dimensions are [3, width, height], so the
	# memory layout is (height, width, 3) and ArraySize0_RBV reads 3. Treating
	# that first value as the width is what makes a colour camera come out
	# unrecognisable.
	n = data.size
	if n == 0:
		return None, 'the array is empty'

	def asInt(value):
		try:
			return int(value) if value else 0
		except (TypeError, ValueError):
			return 0

	dims = [asInt(d) for d in (list(dims) + [0, 0, 0])[:3]]
	mode = normalizeColorMode(colorMode)

	# A colour camera can be recognised from the dimensions alone when
	# ColorMode_RBV is missing: a 3 in one of the first three slots, alongside
	# two plausible picture dimensions.
	if mode is None:
		if dims[0] == 3 and dims[1] > 1 and dims[2] > 1:
			mode = COLOR_MODE_RGB1
		elif dims[1] == 3 and dims[0] > 1 and dims[2] > 1:
			mode = COLOR_MODE_RGB2
		elif dims[2] == 3 and dims[0] > 1 and dims[1] > 1:
			mode = COLOR_MODE_RGB3

	if mode in (COLOR_MODE_RGB1, COLOR_MODE_RGB2, COLOR_MODE_RGB3):
		img, note = reshapeColor(data, dims, mode)
		if img is not None:
			return img, note
		# Fall through to the mono paths rather than giving up, so a camera
		# with an odd colour setup still shows something.

	# Mono, Bayer, or an unknown mode. Bayer is the raw sensor mosaic, which is
	# shown as it is: demosaicing it would be guesswork about the filter order
	# and it is still perfectly usable for a knife edge.
	width, height = dims[0], dims[1]
	note = 'Bayer, shown undemosaiced' if mode == COLOR_MODE_BAYER else ''

	# A tiny leading dimension alongside a populated third dimension is a colour
	# layout this code did not recognise, not a 3 pixel wide picture. Guessing a
	# width of 3 or 4 there produces a meaningless sliver, so prefer the
	# inference paths below. A genuinely narrow mono image has dims[2] == 0 and
	# is left alone.
	if 0 < width <= 4 and height > 4 and dims[2] > 1:
		width = 0
		note = addNote(note, 'unrecognised colour layout {}'.format(dims))

	if width > 0 and height > 0 and width * height <= n:
		return data[:width * height].reshape((height, width)), note

	# Do not count on both size PVs being populated. Fall back to inferring the
	# other dimension from the array length.
	if height > 0 and n % height == 0:
		return data.reshape((height, n // height)), addNote(note, 'width inferred')

	if width > 0 and n % width == 0:
		return data.reshape((n // width, width)), addNote(note, 'height inferred')

	side = int(np.sqrt(n))
	if side > 0:
		return data[:side * side].reshape((side, side)), \
			addNote(note, 'size PVs unusable, square guess')

	return None, 'cannot work out the image shape from {} points'.format(n)


def reshapeColor(data, dims, mode):
	# Returns (height, width, 3) for any of the three RGB layouts, or
	# (None, reason) if the dimensions do not add up.
	#
	# The dims list is in areaDetector order, fastest varying first, so the
	# numpy shape is that list reversed.
	if mode == COLOR_MODE_RGB1:
		# [3, width, height] -> (height, width, 3). Already the layout
		# pyqtgraph wants, so no transpose needed.
		channels, width, height = dims[0], dims[1], dims[2]
		shape = (height, width, channels)
		axes = None
	elif mode == COLOR_MODE_RGB2:
		# [width, 3, height] -> (height, 3, width)
		width, channels, height = dims[0], dims[1], dims[2]
		shape = (height, channels, width)
		axes = (0, 2, 1)
	else:
		# RGB3: [width, height, 3] -> (3, height, width)
		width, height, channels = dims[0], dims[1], dims[2]
		shape = (channels, height, width)
		axes = (1, 2, 0)

	if channels != 3 or width < 1 or height < 1:
		return None, 'colour dimensions {} do not look right'.format(dims)

	expected = width * height * channels
	if expected > data.size:
		return None, 'colour frame needs {} points but only {} arrived'.format(
			expected, data.size)

	img = data[:expected].reshape(shape)
	if axes is not None:
		img = np.transpose(img, axes)

	name = COLOR_MODE_NAMES.get(mode, str(mode))
	return img, 'colour ({})'.format(name)


def addNote(existing, extra):
	if existing and extra:
		return existing + ', ' + extra
	return existing or extra


def toMono(img):
	# Luminance from an RGB frame. A knife edge scan measures total intensity,
	# so a single channel view is often what you actually want to look at, and
	# the histogram and min/max are meaningful again.
	if img.ndim != 3:
		return img
	weights = np.array([0.299, 0.587, 0.114])
	channels = min(img.shape[2], 3)
	return np.tensordot(img[:, :, :channels].astype(np.float32),
		weights[:channels], axes=([2], [0]))


def slowWheelZoom(viewBox, divisor=WHEEL_ZOOM_DIVISOR):
	# Divide a ViewBox's wheel zoom rate. Returns True if it took.
	#
	# There is no public setter for this in pyqtgraph 0.13.3 - wheelEvent reads
	# state['wheelScaleFactor'] directly and nothing else writes it - so the
	# state key is the only way in. It is built once in ViewBox.__init__ and
	# setState only ever updates the dict, so setting it once holds for the life
	# of the view and survives setImage, autoRange and a camera change.
	#
	# Guarded because it is reaching into another library's internals: a later
	# pyqtgraph that renames the key leaves the zoom at its default rate, which
	# is merely the old annoyance back, not a broken window.
	try:
		state = viewBox.state
		if 'wheelScaleFactor' not in state:
			return False
		state['wheelScaleFactor'] = state['wheelScaleFactor'] / float(divisor)
		return True
	except (AttributeError, TypeError, ZeroDivisionError):
		return False


def quarterTurns(rotation):
	# Rotation in degrees -> number of 90 degree clockwise turns. Anything that
	# is not a multiple of 90 rounds to the nearest one, because the alternative
	# is interpolating pixels and this transform must not invent any.
	try:
		turns = int(round(float(rotation) / 90.0))
	except (TypeError, ValueError):
		return 0
	return turns % 4


def orientImage(img, mirror, rotation):
	# Mirror horizontally, then rotate clockwise. Pure reshaping: every output
	# pixel is an input pixel, so the histogram, the levels and the min and max
	# readings are untouched by this.
	#
	# Works on a colour frame as well as a mono one, because both operations are
	# written in terms of the first two axes only and leave a trailing colour
	# axis alone. np.rot90's default axes are (0, 1), which is exactly that.
	#
	# A quarter turn SWAPS the width and the height, which is why the point
	# transform below has to be handed the dimensions of the frame going in.
	if img is None:
		return None

	if mirror:
		# Reverse the x axis, which is axis 1 in row-major order.
		img = img[:, ::-1]

	turns = quarterTurns(rotation)
	if turns:
		# k is negated because np.rot90 turns anticlockwise and the labels in
		# ORIENTATIONS are clockwise, the direction a camera gets rotated in.
		img = np.rot90(img, -turns)

	# A reversed slice or a rot90 is a view with negative or swapped strides.
	# Qt wants contiguous memory for the image it is about to upload, and
	# pyqtgraph would copy it anyway further down, so do it once here.
	return np.ascontiguousarray(img)


def orientPoint(x, y, width, height, mirror, rotation):
	# The same transform as orientImage, applied to one point, so a marker ends
	# up over the pixel it was over before. width and height are those of the
	# frame BEFORE the transform.
	#
	# Done as pixel index to pixel index, hence the minus one: the cross PVs are
	# integer sensor pixels, so the marker belongs on a pixel and not on the
	# boundary beside it. Mirroring pixel 0 of a 1292 wide frame gives pixel
	# 1291, not 1292, which would be off the edge.
	#
	# Each quarter turn is applied in turn rather than as one case per
	# orientation, because then it cannot disagree with np.rot90 for some of the
	# eight and agree for the rest: one turn clockwise sends (x, y) to
	# (height - 1 - y, x) and swaps the frame's dimensions, and that is the only
	# piece of geometry written down here.
	if mirror:
		x = (width - 1) - x

	for _ in range(quarterTurns(rotation)):
		x, y = (height - 1) - y, x
		width, height = height, width

	return x, y


def makePlaceholder(width=320, height=240):
	# Static grey field with faint diagonal stripes, shown when a camera is not
	# connected. Built once, never regenerated. Drawn with fixed levels rather
	# than auto levels, otherwise its narrow value range gets stretched to full
	# contrast and the stripes come out as harsh black and white.
	rows, cols = np.mgrid[0:height, 0:width]
	img = 90.0 + 18.0 * (((rows + cols) // 12) % 2)
	return img


class CameraPanel(QWidget):

	def __init__(self, parent=None):
		super(CameraPanel, self).__init__(parent)

		self.feeds = {}
		self.markerFeeds = {}
		self.placeholder = makePlaceholder()
		self.showingPlaceholder = False
		self.needLevels = True

		self.cameraCombo = QComboBox()
		for cam in CAMERAS:
			self.cameraCombo.addItem(cam['label'], cam['prefix'])
		# Selected before the signal is connected, so building the window does
		# not fire a camera change.
		startIndex = self.cameraCombo.findText(DEFAULT_CAMERA)
		if startIndex >= 0:
			self.cameraCombo.setCurrentIndex(startIndex)
		self.cameraCombo.currentIndexChanged.connect(self.onCameraChanged)

		# Display only, and the tooltip says so: a scan measures a data PV, not
		# this image, so turning the view cannot change a fit or a motor sense.
		self.orientCombo = QComboBox()
		for label, mirror, rotation in ORIENTATIONS:
			self.orientCombo.addItem(label, (mirror, rotation))
		self.orientCombo.setCurrentIndex(0)
		self.orientCombo.setToolTip(
			'Mirror and rotate the displayed image.\n'
			'Display only: it moves the pixels on screen and the markers with\n'
			'them, and changes nothing a scan measures.')
		self.orientCombo.currentIndexChanged.connect(self.onOrientationChanged)

		self.autoLevelsBox = QCheckBox('Auto levels')
		self.autoLevelsBox.setChecked(True)
		self.autoLevelsBox.setToolTip(
			'Rescale the display range on the next frame.\n'
			'Left on, the range follows the image; the histogram on the right\n'
			'can also be dragged to set it by hand.')
		self.autoLevelsBox.toggled.connect(self.onAutoLevelsToggled)

		# Only useful on a colour camera, so it is enabled when one turns up.
		self.monoBox = QCheckBox('As mono')
		self.monoBox.setChecked(False)
		self.monoBox.setEnabled(False)
		self.monoBox.setToolTip(
			'Show a colour camera as luminance.\n'
			'A knife edge measures total intensity, so the single channel view\n'
			'makes the histogram and the min and max readings meaningful.')
		self.monoBox.toggled.connect(self.onMonoToggled)

		# Display only. The markers belong to the camera IOC and the screens that
		# own them; nothing here writes a position, so this just hides the
		# overlay locally when it is sitting on top of the spot you are looking
		# at.
		self.markersBox = QCheckBox('Markers')
		self.markersBox.setChecked(True)
		self.markersBox.setToolTip(
			'Show the camera\'s cross markers.\n'
			'Read only: these come from the camera IOC and cannot be moved here.')
		self.markersBox.toggled.connect(self.onMarkersToggled)

		self.resetViewButton = QPushButton('Reset view')
		self.resetViewButton.clicked.connect(self.onResetView)

		self.indicator = QLabel('Disconnected')
		self.indicator.setAlignment(Qt.AlignCenter)
		self.setIndicator(False)

		# Two rows, not one. One row of all of this needs about 1100 px while the
		# camera side of the default splitter is 640, so everything past the
		# camera name was being clipped: 'Orientatio', 'Auto le', 'Reset viev'
		# and a connection badge reading 'onnecte'. It was already over full with
		# the old Rate box and the longer label pushed it further. The two
		# dropdowns, which are the ones with text that has to be readable, get
		# the first row, and the checkboxes and buttons the second.
		pickRow = QHBoxLayout()
		pickRow.setContentsMargins(0, 0, 0, 0)
		pickRow.addWidget(QLabel('Camera:'))
		pickRow.addWidget(self.cameraCombo)
		pickRow.addWidget(QLabel('Orientation:'))
		pickRow.addWidget(self.orientCombo)
		pickRow.addStretch(1)
		pickRow.addWidget(self.indicator)

		viewRow = QHBoxLayout()
		viewRow.setContentsMargins(0, 0, 0, 0)
		viewRow.addWidget(self.autoLevelsBox)
		viewRow.addWidget(self.monoBox)
		viewRow.addWidget(self.markersBox)
		viewRow.addWidget(self.resetViewButton)
		viewRow.addStretch(1)

		self.imageView = pg.ImageView()
		self.imageView.ui.roiBtn.hide()
		self.imageView.ui.menuBtn.hide()

		# Gentler wheel zoom on the image. Once, here: the state key it sets
		# lives as long as the ViewBox does. Only the image view, not the
		# histogram beside it, whose wheel zooms the level axis and was not the
		# thing overshooting.
		slowWheelZoom(self.imageView.getView())

		# One scatter item per marker colour, built once and given new positions
		# on each tick rather than removed and re-added. ImageView.setImage
		# leaves items that were added to the ViewBox alone, so the crosses
		# survive every frame and only have to be moved when a marker does.
		#
		# One item each rather than a single item with a list of pens, because a
		# per point pen list silently misdraws here: two of four crosses came
		# out the wrong colour and one degenerated into a dash. A fixed pen per
		# item is four cheap items and it renders correctly.
		#
		# pxMode keeps each cross a fixed size in screen pixels, so it stays a
		# marker when you zoom in rather than growing into a pair of bars. Added
		# with ignoreBounds, or a marker outside the frame would enlarge the
		# auto range and shrink the image to fit it.
		symbol = markerSymbol()
		view = self.imageView.getView()
		self.markerItems = []
		for color in MARKER_COLORS:
			item = pg.ScatterPlotItem(symbol=symbol, size=MARKER_SIZE,
				pen=pg.mkPen(color, width=MARKER_WIDTH), brush=None, pxMode=True)
			item.setZValue(20)
			view.addItem(item, ignoreBounds=True)
			self.markerItems.append(item)

		self.statusLabel = QLabel('{} | connecting...'.format(self.currentPrefix()))
		statusFont = QFont('Monospace')
		statusFont.setStyleHint(QFont.TypeWriter)
		self.statusLabel.setFont(statusFont)
		self.statusLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)

		layout = QVBoxLayout()
		layout.addLayout(pickRow)
		layout.addLayout(viewRow)
		layout.addWidget(self.imageView, 1)
		layout.addWidget(self.statusLabel)
		self.setLayout(layout)

		self.showPlaceholder()

	def currentPrefix(self):
		return self.cameraCombo.currentData()

	def currentFeed(self):
		# Built on first use and then cached, so switching back to a camera you
		# were already looking at is instant and does not re-open channels.
		prefix = self.currentPrefix()
		if prefix not in self.feeds:
			self.feeds[prefix] = CameraFeed(prefix)
		return self.feeds[prefix]

	def currentMarkerFeed(self):
		# Cached per camera for the same reason as currentFeed.
		prefix = self.currentPrefix()
		if prefix not in self.markerFeeds:
			self.markerFeeds[prefix] = MarkerFeed(prefix)
		return self.markerFeeds[prefix]

	def currentOrientation(self):
		# (mirror, rotation). Falls back to no transform, which is what a combo
		# built with no items would otherwise crash on in a test.
		data = self.orientCombo.currentData()
		if not data:
			return False, 0
		return data[0], data[1]

	def onOrientationChanged(self):
		self.needLevels = True
		# A quarter turn swaps the width and the height, so the old view range
		# is the wrong shape for the new frame and the image would come up
		# half off screen until somebody reset the view by hand. needLevels
		# already makes the next setImage auto range, but the markers are
		# cleared here so none of them sits on the old pixel for a tick.
		self.drawMarkers([])

		window = self.window()
		if isinstance(window, QMainWindow):
			window.statusBar().showMessage(
				'Image orientation: {} (display only)'.format(
					self.orientCombo.currentText()), 2000)

	def onCameraChanged(self):
		self.needLevels = True
		self.showingPlaceholder = False
		# Cleared rather than left in place, or the markers of the camera you
		# just left would sit over the new image until the next tick.
		self.drawMarkers([])
		self.statusLabel.setText('{} | connecting...'.format(self.currentPrefix()))
		self.refresh()

	def onAutoLevelsToggled(self, checked):
		if checked:
			self.needLevels = True

	def onMonoToggled(self, checked):
		# Switching between colour and luminance changes the value range, so
		# the levels have to be recomputed or the image goes black.
		self.needLevels = True

	def onMarkersToggled(self, checked):
		# Takes effect at once rather than on the next frame, which matters when
		# the markers are hiding whatever you just turned them off to see.
		if not checked:
			self.drawMarkers([])

	def onResetView(self):
		self.needLevels = True
		self.imageView.autoRange()

	def drawMarkers(self, markers):
		# markers is a list of (x, y, number) in image pixels, where number is
		# the CamViewer cross number and so picks the colour. pyqtgraph is in
		# row-major mode, so a marker at pixel (x, y) goes straight to plot
		# coordinates (x, y) with no transpose.
		#
		# Sorted into one bucket per colour item. Cross 1 is always blue whether
		# or not crosses 2 to 4 are set, which is the whole point of matching
		# CamViewer's colours, so the bucket is chosen by the number and never
		# by position in the list.
		buckets = [[] for _ in self.markerItems]
		for x, y, number in markers:
			buckets[(int(number) - MARKER_FIRST_SLOT) % len(buckets)].append((x, y))

		for item, points in zip(self.markerItems, buckets):
			item.setData(x=[p[0] for p in points], y=[p[1] for p in points])

	def setIndicator(self, connected):
		if connected:
			self.indicator.setText(' Connected ')
			self.indicator.setStyleSheet(
				'background-color: #cdf0cd; color: #184818; border: 1px solid #7aa87a; padding: 2px 6px;')
		else:
			self.indicator.setText(' Disconnected ')
			self.indicator.setStyleSheet(
				'background-color: #f5d0d0; color: #701818; border: 1px solid #c08080; padding: 2px 6px;')

	def showPlaceholder(self):
		# Only push the placeholder on the way into the disconnected state.
		# Re-pushing it every tick would waste work and make the histogram jump.
		if self.showingPlaceholder:
			return
		self.imageView.setImage(self.placeholder, autoLevels=False, autoRange=True)
		self.imageView.setLevels(0, 255)
		self.showingPlaceholder = True
		self.needLevels = True

	def refresh(self):
		feed = self.currentFeed()
		img, status = feed.readImage()

		self.statusLabel.setText('{} | {}'.format(feed.prefix, status))

		if img is None:
			self.setIndicator(False)
			self.showPlaceholder()
			# The placeholder is not this camera's image, so a marker drawn on
			# it would be pointing at nothing.
			self.drawMarkers([])
			return

		self.setIndicator(True)

		# The As mono box only means anything for a colour frame, so it follows
		# whatever the camera is actually sending.
		isColor = (img.ndim == 3)
		if self.monoBox.isEnabled() != isColor:
			self.monoBox.setEnabled(isColor)

		if isColor and self.monoBox.isChecked():
			img = toMono(img)

		# The frame's own dimensions, taken before the orientation is applied,
		# because that is the frame the cross positions are in and a quarter
		# turn swaps the two.
		mirror, rotation = self.currentOrientation()
		frameHeight, frameWidth = img.shape[0], img.shape[1]
		if mirror or quarterTurns(rotation):
			img = orientImage(img, mirror, rotation)

		# Rescale only on the first frame of a camera, or when asked. Doing it
		# every frame makes the display pulse on a noisy image and can hide a
		# dim spot next to a hot pixel.
		if self.needLevels:
			self.imageView.setImage(img, autoLevels=True, autoRange=True)
			self.needLevels = False
			if self.autoLevelsBox.isChecked():
				self.autoLevelsBox.setChecked(False)
		else:
			self.imageView.setImage(img, autoLevels=False, autoRange=False,
				autoHistogramRange=False)

		self.showingPlaceholder = False

		# After setImage, so a marker is never drawn over a frame it does not
		# belong to. Read every tick because an overlay can be moved from the
		# camera screens while this window is open, and the whole point of
		# showing it is that it agrees with those screens.
		if self.markersBox.isChecked():
			markers = self.currentMarkerFeed().readMarkers()
			# Through the same transform as the pixels, so a cross stays on the
			# feature it was on. readMarkers has already taken the ROI origin
			# off, so these are coordinates in the frame whose size was just
			# measured.
			if mirror or quarterTurns(rotation):
				markers = [orientPoint(x, y, frameWidth, frameHeight,
					mirror, rotation) + (number,)
					for x, y, number in markers]
			self.drawMarkers(markers)


class MotorEditDialog(QDialog):

	# Opened by double clicking a motor row. Edits the PV, the name, and either
	# the scan range or the fixed position. Nothing is written to the spec until
	# OK is pressed, so Cancel genuinely cancels.

	def __init__(self, motorSpec, scanIndex, parent=None):
		super(MotorEditDialog, self).__init__(parent)

		self.spec = motorSpec
		self.setWindowTitle('Edit motor - scan {}'.format(scanIndex))
		self.setMinimumWidth(430)

		self.pvEdit = QLineEdit(motorSpec.pv)
		self.nameEdit = QLineEdit(motorSpec.name)

		self.lowEdit = QLineEdit(erfFitConfig.fmtNum(motorSpec.low))
		self.lowEdit.setValidator(QDoubleValidator())
		self.highEdit = QLineEdit(erfFitConfig.fmtNum(motorSpec.high))
		self.highEdit.setValidator(QDoubleValidator())
		self.stepsEdit = QLineEdit('' if motorSpec.numSteps is None else str(motorSpec.numSteps))
		self.stepsEdit.setValidator(QIntValidator(2, 100000))
		self.valueEdit = QLineEdit(erfFitConfig.fmtNum(motorSpec.value))
		self.valueEdit.setValidator(QDoubleValidator())

		self.stepLabel = QLabel('')
		self.stepLabel.setStyleSheet('color: #555555;')
		for widget in (self.lowEdit, self.highEdit, self.stepsEdit):
			widget.textChanged.connect(self.updateStepSize)

		self.problemLabel = QLabel('')
		self.problemLabel.setStyleSheet('color: #a03030;')
		self.problemLabel.setWordWrap(True)

		form = QGridLayout()
		row = 0
		form.addWidget(QLabel('Motor PV'), row, 0)
		form.addWidget(self.pvEdit, row, 1)
		row += 1
		form.addWidget(QLabel('Name'), row, 0)
		form.addWidget(self.nameEdit, row, 1)
		row += 1

		if motorSpec.scanning:
			form.addWidget(QLabel('Start'), row, 0)
			form.addWidget(self.lowEdit, row, 1)
			row += 1
			form.addWidget(QLabel('End'), row, 0)
			form.addWidget(self.highEdit, row, 1)
			row += 1
			form.addWidget(QLabel('Number of steps'), row, 0)
			form.addWidget(self.stepsEdit, row, 1)
			row += 1
			form.addWidget(QLabel('Step size'), row, 0)
			form.addWidget(self.stepLabel, row, 1)
			row += 1
		else:
			form.addWidget(QLabel('Fixed position'), row, 0)
			form.addWidget(self.valueEdit, row, 1)
			row += 1

		roleNote = QLabel('This motor {} in this scan. Scanning is set by the '
			'indep flag in the config file.'.format(
				'scans' if motorSpec.scanning else 'is held fixed'))
		roleNote.setStyleSheet('color: #555555;')
		roleNote.setWordWrap(True)

		self.okButton = QPushButton('OK')
		self.okButton.setDefault(True)
		self.okButton.clicked.connect(self.onAccept)
		cancelButton = QPushButton('Cancel')
		cancelButton.clicked.connect(self.reject)

		buttons = QHBoxLayout()
		buttons.addStretch(1)
		buttons.addWidget(self.okButton)
		buttons.addWidget(cancelButton)

		layout = QVBoxLayout()
		layout.addLayout(form)
		layout.addWidget(roleNote)
		layout.addWidget(self.problemLabel)
		layout.addLayout(buttons)
		self.setLayout(layout)

		self.updateStepSize()

	def readNumbers(self):
		# Returns (low, high, numSteps) with None for anything unreadable.
		def toFloat(text):
			try:
				return float(text)
			except ValueError:
				return None

		def toInt(text):
			try:
				return int(text)
			except ValueError:
				return None

		return toFloat(self.lowEdit.text()), toFloat(self.highEdit.text()), toInt(self.stepsEdit.text())

	def updateStepSize(self):
		low, high, steps = self.readNumbers()
		if low is None or high is None or steps is None or steps < 2:
			self.stepLabel.setText('--')
			return
		self.stepLabel.setText('{} per step over {} points'.format(
			erfFitConfig.fmtNum((high - low) / (steps - 1), 5), steps))

	def onAccept(self):
		# Validate against a scratch copy so a rejected edit leaves the real
		# spec untouched.
		problems = []

		pv = self.pvEdit.text().strip()
		name = self.nameEdit.text().strip()

		if not pv:
			problems.append('Motor PV cannot be empty.')
		elif ',' in pv:
			problems.append('Motor PV cannot contain a comma.')
		if ',' in name:
			problems.append('Name cannot contain a comma.')

		if self.spec.scanning:
			low, high, steps = self.readNumbers()
			if low is None:
				problems.append('Start is not a number.')
			if high is None:
				problems.append('End is not a number.')
			if steps is None:
				problems.append('Number of steps is not a whole number.')
			elif steps < 2:
				problems.append('Number of steps must be at least 2.')
			if low is not None and high is not None and low == high:
				problems.append('Start and end are the same, so there is nothing to scan.')
		else:
			try:
				value = float(self.valueEdit.text())
			except ValueError:
				problems.append('Fixed position is not a number.')
				value = None

		if problems:
			self.problemLabel.setText('\n'.join(problems))
			return

		self.spec.applyEdit('pv', pv)
		self.spec.applyEdit('name', name)
		if self.spec.scanning:
			self.spec.applyEdit('low', low)
			self.spec.applyEdit('high', high)
			self.spec.applyEdit('numSteps', steps)
		else:
			self.spec.applyEdit('value', value)

		self.accept()


class DataPVEditDialog(QDialog):

	# Opened by double clicking a data PV row, or a scan header to edit the
	# number of averages.

	def __init__(self, scanSpec, pvIndex, parent=None):
		super(DataPVEditDialog, self).__init__(parent)

		self.scan = scanSpec
		self.pvIndex = pvIndex

		self.setWindowTitle('Edit scan {} collection'.format(scanSpec.index))
		self.setMinimumWidth(430)

		self.pvEdit = QLineEdit('' if pvIndex is None else scanSpec.dataPVs[pvIndex])
		self.avgsEdit = QLineEdit(str(scanSpec.numAvgs))
		self.avgsEdit.setValidator(QIntValidator(1, 100000))

		self.problemLabel = QLabel('')
		self.problemLabel.setStyleSheet('color: #a03030;')
		self.problemLabel.setWordWrap(True)

		form = QGridLayout()
		row = 0
		if pvIndex is not None:
			form.addWidget(QLabel('Data PV'), row, 0)
			form.addWidget(self.pvEdit, row, 1)
			row += 1
		form.addWidget(QLabel('Averages per point'), row, 0)
		form.addWidget(self.avgsEdit, row, 1)

		note = QLabel('')
		if pvIndex is not None and pvIndex > 0:
			note.setText('Note erfFit.runConfig only reads the first data PV of a scan.')
			note.setStyleSheet('color: #906030;')
			note.setWordWrap(True)

		self.okButton = QPushButton('OK')
		self.okButton.setDefault(True)
		self.okButton.clicked.connect(self.onAccept)
		cancelButton = QPushButton('Cancel')
		cancelButton.clicked.connect(self.reject)

		buttons = QHBoxLayout()
		buttons.addStretch(1)
		buttons.addWidget(self.okButton)
		buttons.addWidget(cancelButton)

		layout = QVBoxLayout()
		layout.addLayout(form)
		if note.text():
			layout.addWidget(note)
		layout.addWidget(self.problemLabel)
		layout.addLayout(buttons)
		self.setLayout(layout)

	def onAccept(self):
		problems = []

		pv = self.pvEdit.text().strip()
		if self.pvIndex is not None:
			if not pv:
				problems.append('Data PV cannot be empty.')
			elif ',' in pv:
				problems.append('Data PV cannot contain a comma.')

		try:
			avgs = int(self.avgsEdit.text())
			if avgs < 1:
				problems.append('Averages per point must be at least 1.')
		except ValueError:
			problems.append('Averages per point is not a whole number.')
			avgs = None

		if problems:
			self.problemLabel.setText('\n'.join(problems))
			return

		if self.pvIndex is not None:
			self.scan.setDataPV(self.pvIndex, pv)
		self.scan.setNumAvgs(avgs)

		self.accept()


class ConfigTree(QTreeWidget):

	# A tree rather than a flat table: a config is scans containing motors and
	# data PVs, and flattening a 2 scan by 2 motor config into one table means
	# either repeating the scan columns or leaving holes.
	#
	# Double clicking a row opens the matching edit dialog. Rows carry their
	# spec object in the item data so the handler knows what was clicked.

	HEADERS = ['Item', 'PV', 'Name', 'Role', 'Positions', 'Notes']

	# Roles for stashing what a row refers to, so the double click handler can
	# tell a motor row from a data PV row.
	ROLE_KIND = Qt.UserRole
	ROLE_SCAN = Qt.UserRole + 1
	ROLE_INDEX = Qt.UserRole + 2

	def __init__(self, parent=None):
		super(ConfigTree, self).__init__(parent)
		self.setColumnCount(len(self.HEADERS))
		self.setHeaderLabels(self.HEADERS)
		self.setAlternatingRowColors(True)
		self.setRootIsDecorated(True)
		self.setUniformRowHeights(True)

		self.doc = None
		self.editCallback = None
		self.itemDoubleClicked.connect(self.onItemDoubleClicked)

	def showDoc(self, doc):
		self.doc = doc
		self.rebuild()

	def rebuild(self):
		# Redrawn from the spec objects after every edit, so the tree is always
		# a view of the current state rather than something kept in step by hand.
		self.clear()

		if self.doc is None:
			return

		for scan in self.doc.scans:
			scanItem = QTreeWidgetItem(['Scan {}'.format(scan.index), '', '', '', '', scan.summary()])
			font = scanItem.font(0)
			font.setBold(True)
			for col in range(len(self.HEADERS)):
				scanItem.setFont(col, font)
				scanItem.setBackground(col, QBrush(COLOR_HEADER))
			scanItem.setData(0, self.ROLE_KIND, 'scan')
			scanItem.setData(0, self.ROLE_SCAN, scan.index)
			scanItem.setToolTip(0, 'Double click to change the averages per point')
			if scan.editedNumAvgs:
				self.markEdited(scanItem, 5, 'averages changed from {}'.format(scan.originalNumAvgs))
			self.addTopLevelItem(scanItem)

			for motorNum, motor in enumerate(scan.motors):
				self.addMotorItem(scanItem, scan, motorNum, motor)

			for pvNum, dataPV in enumerate(scan.dataPVs):
				# erfFit.runConfig only ever reads dataPVList[0], so say so
				# rather than implying every listed PV gets collected.
				note = '' if pvNum == 0 else 'not read by erfFit, only the first data PV is used'
				pvItem = QTreeWidgetItem(
					['Data PV {}'.format(pvNum + 1), dataPV, '', '', '', note])
				pvItem.setData(0, self.ROLE_KIND, 'dataPV')
				pvItem.setData(0, self.ROLE_SCAN, scan.index)
				pvItem.setData(0, self.ROLE_INDEX, pvNum)
				pvItem.setToolTip(0, 'Double click to change this data PV')
				if note:
					self.paintRow(pvItem, COLOR_WARNING)
				if scan.isDataPVEdited(pvNum):
					was = scan.originalDataPVs[pvNum] if pvNum < len(scan.originalDataPVs) else ''
					self.markEdited(pvItem, 1, 'was {}'.format(was))
				scanItem.addChild(pvItem)

			if scan.warnings:
				for warning in scan.warnings:
					warnItem = QTreeWidgetItem(['', '', '', '', '', warning])
					warnItem.setToolTip(5, warning)
					self.paintRow(warnItem, COLOR_WARNING)
					scanItem.addChild(warnItem)

		self.expandAll()
		for col in range(len(self.HEADERS)):
			self.resizeColumnToContents(col)

		# Scan range is the whole point of this view, so make sure Positions
		# and the columns before it do not get squeezed out.
		for col, minWidth in ((1, 170), (4, 120)):
			if self.columnWidth(col) < minWidth:
				self.setColumnWidth(col, minWidth)

	def addMotorItem(self, scanItem, scan, motorNum, motor):
		item = QTreeWidgetItem([
			'Motor {}'.format(motorNum + 1),
			motor.pv,
			motor.name,
			motor.roleText(),
			motor.positionText(),
			motor.detailText(),
		])

		item.setData(0, self.ROLE_KIND, 'motor')
		item.setData(0, self.ROLE_SCAN, scan.index)
		item.setData(0, self.ROLE_INDEX, motorNum)
		item.setToolTip(0, 'Double click to edit the PV, range and steps')

		# Scanning is marked three ways at once, text, weight and colour, so it
		# never depends on colour alone.
		if motor.scanning:
			font = item.font(0)
			font.setBold(True)
			for col in range(len(self.HEADERS)):
				item.setFont(col, font)
			item.setBackground(3, QBrush(COLOR_SCANNING))

		if motor.nameSynth and not motor.isEdited():
			nameFont = item.font(2)
			nameFont.setItalic(True)
			item.setFont(2, nameFont)
			item.setForeground(2, QBrush(COLOR_SYNTH))
			item.setToolTip(2, 'This line has no motor name (legacy 3 field format).\n'
				'The name shown was generated from the PV.')

		if motor.warnings:
			text = '; '.join(motor.warnings)
			item.setText(5, text if not motor.detailText() else motor.detailText() + ' | ' + text)
			self.paintRow(item, COLOR_WARNING)
			item.setToolTip(5, '\n'.join(motor.warnings))

		# Edited fields get the changed colour and a note saying what they were,
		# which matters when deciding whether to save over the original file.
		if motor.isEdited():
			columnForField = {'pv': 1, 'name': 2, 'low': 4, 'high': 4,
				'numSteps': 5, 'value': 4}
			wasParts = []
			for field in sorted(motor.edited):
				col = columnForField.get(field)
				if col is not None:
					self.markEdited(item, col, None)
				wasParts.append('{} was {}'.format(field, motor.originalText(field) or '(empty)'))

			note = item.text(5)
			changedNote = 'CHANGED: ' + ', '.join(wasParts)
			item.setText(5, (note + ' | ' if note else '') + changedNote)
			item.setToolTip(5, changedNote)

		problems = motor.validate()
		if problems:
			self.paintRow(item, COLOR_WARNING)
			item.setText(5, item.text(5) + ' | PROBLEM: ' + '; '.join(problems))
			item.setToolTip(5, '\n'.join(problems))

		scanItem.addChild(item)

	def markEdited(self, item, column, tooltip):
		item.setBackground(column, QBrush(COLOR_EDITED))
		item.setForeground(column, QBrush(COLOR_EDITED_TEXT))
		font = item.font(column)
		font.setBold(True)
		item.setFont(column, font)
		if tooltip:
			item.setToolTip(column, tooltip)

	def onItemDoubleClicked(self, item, column):
		if self.doc is None:
			return

		kind = item.data(0, self.ROLE_KIND)
		scanIndex = item.data(0, self.ROLE_SCAN)
		if kind is None or scanIndex is None:
			return

		scan = None
		for candidate in self.doc.scans:
			if candidate.index == scanIndex:
				scan = candidate
				break
		if scan is None:
			return

		if kind == 'motor':
			motorNum = item.data(0, self.ROLE_INDEX)
			dialog = MotorEditDialog(scan.motors[motorNum], scan.index, self)
		elif kind == 'dataPV':
			dialog = DataPVEditDialog(scan, item.data(0, self.ROLE_INDEX), self)
		elif kind == 'scan':
			dialog = DataPVEditDialog(scan, None, self)
		else:
			return

		if dialog.exec_() == QDialog.Accepted:
			self.rebuild()
			if self.editCallback is not None:
				self.editCallback()

	def paintRow(self, item, color):
		for col in range(len(self.HEADERS)):
			item.setBackground(col, QBrush(color))


def briefError(error):
	# ophyd exception text can run to several lines and the row has one short
	# label, so keep the first line only. The exception type goes FIRST,
	# because LimitError and MotorDisabledError mean different fixes and the
	# name must survive the label being trimmed.
	text = str(error).strip().splitlines()
	text = text[0] if text else ''
	name = type(error).__name__ if isinstance(error, BaseException) else ''
	if name and name not in ('Exception', 'RuntimeError'):
		if text:
			return '{}: {}'.format(name, text)
		return name
	return text or 'unknown error'


def elideText(text, limit):
	# Scan labels come from the saved filename, which carries the motor name and
	# a timestamp, so they get long. Cut the middle rather than the tail: the end
	# is what distinguishes one scan of a pair from the other.
	if len(text) <= limit:
		return text
	if limit <= 3:
		return text[:limit]
	keep = limit - 3
	head = (keep + 1) // 2
	return text[:head] + '...' + text[len(text) - (keep - head):]


def deviceName(spec):
	# ophyd device names have to be valid python identifiers, and the configs
	# contain names like 'X Motor', so build one from the name with the PV as a
	# fallback. Only used for the ophyd object; the display still shows the
	# name from the config.
	text = spec.name if spec.name else spec.pv
	cleaned = ''.join([c if (c.isalnum() or c == '_') else '_' for c in text])
	cleaned = cleaned.strip('_')
	if not cleaned or cleaned[0].isdigit():
		cleaned = 'motor_' + cleaned
	return cleaned


class MotorDevices:

	# Owns the erfFit import and one device object per motor PV.
	#
	# erfFit is imported HERE rather than at the top of the file, on purpose.
	# It pulls in pcdsdevices, which is not installed everywhere, and importing
	# it is only appropriate once the user has actually asked to connect to
	# motors. Loading, editing and saving a config stays completely free of
	# hardware, which is what erfFitConfig.py is for.
	#
	# Devices are built by erfFit.getMotor into self.devices, which is exactly
	# the master list erfFit.loadConfig and runConfig take, so passing this dict
	# to a scan makes it drive the same objects these rows poll.

	def __init__(self):
		self.module = None
		self.error = None
		self.devices = {}

	def load(self):
		# Returns the erfFit module, or None with self.error set.
		if self.module is not None or self.error is not None:
			return self.module

		# erfFit.py usually lives beside the GUI, but it is also commonly one
		# directory up, with the GUI in its own folder. Add the parent to the
		# path so that layout works without copying the file around. An
		# ERFFIT_MODULE_DIR override is honoured for anywhere else.
		for extra in erfFitPaths.moduleSearchDirs():
			if extra not in sys.path:
				sys.path.append(extra)

		try:
			import erfFit
			self.module = erfFit
		except Exception as e:
			# ImportError for a missing pcdsdevices, but a broken dependency
			# can raise plenty else, and none of it should stop the GUI.
			self.error = '{}: {}'.format(type(e).__name__, e)

		return self.module

	def device(self, spec):
		# One device per PV, built once and kept. Returns (device, error).
		if spec.pv in self.devices:
			return self.devices[spec.pv], None

		module = self.load()
		if module is None:
			return None, self.error

		try:
			# Constructing this connects to the motor record. It does not move
			# anything.
			#
			# getMotor puts the device into self.devices itself, so this dict IS
			# erfFit's master list. Handing the same dict to erfFit.loadConfig
			# or runConfig means a scan drives the very objects these jog rows
			# are already polling, rather than connecting each motor twice.
			device = module.getMotor(spec.pv, deviceName(spec), self.devices)
		except Exception as e:
			return None, '{}: {}'.format(type(e).__name__, e)

		self.devices[spec.pv] = device
		return device, None


class MotorRow(QWidget):

	# One line of jog controls: readback, absolute position entry, step size,
	# and minus/plus buttons.

	def __init__(self, motorSpec, devices, parent=None):
		super(MotorRow, self).__init__(parent)

		self.spec = motorSpec
		self.devices = devices
		self.device = None
		self.lastPosition = None
		self.units = ''
		self.moveStatus = None
		self.moveTarget = None
		self.settleTicks = 0
		self.outcomeTicks = 0
		self.previousPosition = None
		self.lastMessage = ''
		self.joggingLocked = False

		# The config names can be long, and the state label on the right is
		# where move progress and errors appear, so the name and PV are given
		# fixed widths and elided rather than being allowed to push it away.
		# Tight fixed widths, because the state label on the right is where move
		# progress and errors appear and it must not be pushed off the panel.
		# The PV is in the tooltip rather than its own column; the name and the
		# scan list above already identify the motor.
		self.nameLabel = QLabel(motorSpec.name)
		self.nameLabel.setFixedWidth(120)
		self.nameLabel.setToolTip('{} ({})'.format(motorSpec.name, motorSpec.pv))

		self.readback = QLabel('--')
		self.readback.setMinimumWidth(90)
		self.readback.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
		self.readback.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
		rbFont = QFont('Monospace')
		rbFont.setStyleHint(QFont.TypeWriter)
		self.readback.setFont(rbFont)
		self.readback.setToolTip('Current position, from the .RBV field')

		self.absoluteEdit = QLineEdit()
		self.absoluteEdit.setMaximumWidth(90)
		self.absoluteEdit.setValidator(QDoubleValidator())
		self.absoluteEdit.setPlaceholderText('abs')
		self.absoluteEdit.setToolTip('Type an absolute position and press Enter to move there')
		self.absoluteEdit.returnPressed.connect(self.onAbsoluteEntered)

		self.stepEdit = QLineEdit('0.1')
		self.stepEdit.setMaximumWidth(70)
		self.stepEdit.setValidator(QDoubleValidator())
		self.stepEdit.setToolTip('Relative step size. Press Enter to set it, then use - and +')
		self.stepEdit.returnPressed.connect(self.onStepEntered)

		self.minusButton = QPushButton('-')
		self.minusButton.setMaximumWidth(34)
		self.minusButton.setToolTip('Move one step in the negative direction')
		self.minusButton.clicked.connect(self.onMinus)

		self.plusButton = QPushButton('+')
		self.plusButton.setMaximumWidth(34)
		self.plusButton.setToolTip('Move one step in the positive direction')
		self.plusButton.clicked.connect(self.onPlus)

		self.stopButton = QPushButton('Stop')
		self.stopButton.setMaximumWidth(48)
		self.stopButton.setEnabled(False)
		self.stopButton.setToolTip('Stop this motor now')
		self.stopButton.clicked.connect(self.onStop)

		self.stateLabel = QLabel('not connected')
		self.stateLabel.setMinimumWidth(150)
		self.stateLabel.setStyleSheet('color: #777777;')

		layout = QHBoxLayout()
		layout.setContentsMargins(2, 2, 2, 2)
		layout.setSpacing(4)
		layout.addWidget(self.nameLabel)
		layout.addWidget(self.readback)
		layout.addWidget(QLabel('to'))
		layout.addWidget(self.absoluteEdit)
		layout.addWidget(QLabel('step'))
		layout.addWidget(self.stepEdit)
		layout.addWidget(self.minusButton)
		layout.addWidget(self.plusButton)
		layout.addWidget(self.stopButton)
		layout.addWidget(self.stateLabel, 1)
		self.setLayout(layout)

		self.setControlsEnabled(False)

	def setControlsEnabled(self, enabled):
		# joggingLocked wins. refresh() calls this every tick from the readback,
		# so without the lock a running scan would have the jog controls switched
		# back on underneath it.
		for widget in (self.absoluteEdit, self.stepEdit, self.minusButton, self.plusButton):
			widget.setEnabled(enabled and not self.joggingLocked)
		# Stopping stays available whenever there is a device, even when the
		# move controls are disabled for want of a readback.
		self.stopButton.setEnabled(enabled or self.device is not None)

	def setJoggingEnabled(self, enabled):
		self.joggingLocked = not enabled
		if self.joggingLocked:
			self.setToolTip('Jogging is disabled while a scan is running')
		else:
			self.setToolTip('')

		# refresh() decides enablement from the readback and runs every tick, so
		# the lock only has to be applied here and dropped here. Re-deriving the
		# right state would mean duplicating what refresh already knows.
		self.setControlsEnabled(not self.joggingLocked and self.lastPosition is not None)

	def connectMotor(self):
		# Builds the ophyd device. This connects to the motor record but does
		# not move anything.
		if self.device is None:
			device, error = self.devices.device(self.spec)
			if device is None:
				self.device = None
				self.setState('cannot connect', '#a03030')
				self.setToolTip(error or '')
				return None
			self.device = device
			self.setToolTip('')
		self.setState('connecting...', '#777777')
		return self.device

	def stepSize(self):
		try:
			return float(self.stepEdit.text())
		except ValueError:
			return None

	def onStepEntered(self):
		step = self.stepSize()
		if step is None:
			self.notify('bad step value')
			return
		self.stepEdit.setText(erfFitConfig.fmtNum(step))
		self.notify('step size set to {}'.format(erfFitConfig.fmtNum(step)))

	def onAbsoluteEntered(self):
		try:
			target = float(self.absoluteEdit.text())
		except ValueError:
			self.notify('bad position value')
			return
		self.requestMove(target, 'absolute')
		# Cleared so a second Enter cannot silently re-send an old number.
		self.absoluteEdit.clear()

	def onMinus(self):
		self.jog(-1)

	def onPlus(self):
		self.jog(1)

	def jog(self, direction):
		# Relative move. mvr() takes the delta and works out the target from
		# the live position itself via wm(), so there is no arithmetic here and
		# no reliance on the cached readback.
		step = self.stepSize()
		if step is None:
			self.notify('set a step size first')
			return
		if self.device is None:
			self.notify('not connected')
			return

		delta = direction * step
		target = None
		if self.lastPosition is not None:
			target = self.lastPosition + delta

		# check_value wants an absolute position, so it can only be used when
		# there is a readback to add the step to. Without one, let the motor
		# record do the limit checking.
		if target is not None and not self.checkTarget(target):
			return

		self.startMove(lambda: self.device.mvr(delta),
			'moving {} {}'.format('+' if delta >= 0 else '-',
				erfFitConfig.fmtNum(abs(delta))), target)

	def requestMove(self, target, kind):
		# Absolute move.
		if self.device is None:
			self.notify('not connected')
			return
		if not self.checkTarget(target):
			return

		self.startMove(lambda: self.device.mv(target),
			'moving to {}'.format(erfFitConfig.fmtNum(target)), target)

	def checkTarget(self, target):
		# check_value covers the soft limits, the .SPG stop/pause state and the
		# disabled flag in one call, raising LimitError or MotorDisabledError.
		# Worth doing before the move: mv() catches LimitError internally and
		# only reports it through the returned status, which is easy to miss.
		try:
			self.device.check_value(target)
		except Exception as e:
			self.notify('refused: {}'.format(briefError(e)))
			return False
		return True

	def startMove(self, moveCall, message, target):
		# THE ONLY PLACE THAT WRITES TO A MOTOR.
		#
		# mv() and mvr() are non-blocking: their wait argument defaults to
		# False, unlike move(), whose wait defaults to True and would freeze
		# the GUI for the whole travel. The returned MoveStatus is polled by
		# refresh() from the shared timer, so progress and completion show up
		# without this thread ever waiting.
		self.outcomeTicks = 0
		self.settleTicks = 0
		self.previousPosition = None

		try:
			self.moveStatus = moveCall()
		except Exception as e:
			self.moveStatus = None
			self.moveTarget = None
			self.notify('move failed: {}'.format(briefError(e)))
			return

		self.moveTarget = target
		self.notify(message)

	def onStop(self):
		if self.device is None:
			return
		try:
			self.device.stop()
		except Exception as e:
			self.notify('stop failed: {}'.format(briefError(e)))
			return
		self.moveStatus = None
		self.moveTarget = None
		self.settleTicks = 0
		self.outcomeTicks = 0
		self.notify('stopped')

	def notify(self, message):
		self.lastMessage = message
		self.setState(message, '#306090')
		window = self.window()
		if isinstance(window, QMainWindow):
			window.statusBar().showMessage('{}: {}'.format(self.spec.name, message), 4000)

	def setOutcome(self, message, color):
		# How a move ended. Held on screen for a few seconds so it does not
		# vanish on the next poll.
		self.setState(message, color)
		self.outcomeTicks = self.outcomeHoldTicks()

	def outcomeHoldTicks(self):
		# About four seconds, worked out from the current poll interval so the
		# hold does not change when the rate does.
		interval = 200
		window = self.window()
		try:
			if window is not None and window.timer.isActive():
				interval = max(window.timer.interval(), 20)
		except Exception:
			pass
		return max(int(4000 / interval), 1)

	def setState(self, message, color):
		# Trimmed for the label, full text in the tooltip, so a long limit
		# error does not stretch the row beyond the panel.
		shown = message if len(message) <= 44 else message[:43] + '...'
		self.stateLabel.setText(shown)
		self.stateLabel.setToolTip(message if shown != message else '')
		self.stateLabel.setStyleSheet('color: {};'.format(color))

	def refresh(self):
		# Called from the shared timer. Returns True when connected.
		#
		# Everything here is a read. Each device access is guarded, because an
		# ophyd signal whose IOC has gone away raises readily and a raise from
		# the timer would take the window down.
		if self.device is None:
			return False

		if not self.isDeviceConnected():
			self.readback.setText('--')
			self.setState('disconnected', '#a03030')
			self.setControlsEnabled(False)
			return False

		position = self.readPosition()
		if position is None:
			self.readback.setText('--')
			self.setState('connected, no readback', '#a06030')
			self.setControlsEnabled(False)
			return True

		self.lastPosition = position
		if not self.units:
			self.units = self.readUnits()
		self.readback.setText('{}{}'.format(
			erfFitConfig.fmtNum(position, 4),
			' ' + self.units if self.units else ''))
		self.setControlsEnabled(True)

		self.updateMoveState(position)
		return True

	def isDeviceConnected(self):
		try:
			return bool(self.device.connected)
		except Exception:
			return False

	def readPosition(self):
		try:
			return float(self.device.position)
		except Exception:
			return None

	def readUnits(self):
		try:
			return str(self.device.egu or '')
		except Exception:
			return ''

	def isMoving(self):
		try:
			return bool(self.device.moving)
		except Exception:
			return None

	def updateMoveState(self, position):
		# The motor record decides when a move is over, not the status object.
		#
		# An earlier version keyed completion off MoveStatus.done, and when
		# that never flipped the row stayed stuck on 'moving -> 3 (5.493 to
		# go)' for ever, with a frozen distance, even though the readback was
		# fine and the motor had stopped. MoveStatus completion rides on ophyd
		# subscriptions, which are not guaranteed to be delivering here, so it
		# is now only consulted for the failure reason. What a move did is
		# worked out from .DMOV and the readback, which are the same things the
		# position display already relies on.
		moving = self.isMoving()

		# No .DMOV on this record, so fall back to whether the readback is
		# still changing. Without this a move on such a motor would be judged
		# finished while it was still travelling.
		if moving is None:
			moving = self.positionIsChanging(position)

		if moving is True:
			self.settleTicks = 0
			if self.moveTarget is not None:
				self.setState('moving -> {} ({} to go)'.format(
					erfFitConfig.fmtNum(self.moveTarget),
					erfFitConfig.fmtNum(abs(self.moveTarget - position), 4)), '#306090')
			else:
				self.setState('moving', '#306090')
			return

		# Not moving. If a move was outstanding, it has finished one way or
		# another, so report and clear it.
		if self.moveTarget is not None:
			# A couple of idle ticks before judging, because .DMOV can still
			# read done in the moment between the put and the motor starting.
			self.settleTicks += 1
			if self.settleTicks < 3:
				return

			target = self.moveTarget
			self.moveTarget = None
			self.settleTicks = 0

			reason = self.statusFailure()
			self.moveStatus = None

			distance = abs(target - position)
			if reason:
				self.setOutcome('move did not finish: {}'.format(reason), '#a03030')
			elif distance <= self.arrivalTolerance():
				self.setOutcome('done at {}'.format(
					erfFitConfig.fmtNum(position, 4)), '#306030')
			else:
				# Stopped short: a limit, a stop, or someone else taking over.
				self.setOutcome('stopped at {}, {} short of {}'.format(
					erfFitConfig.fmtNum(position, 4),
					erfFitConfig.fmtNum(distance, 4),
					erfFitConfig.fmtNum(target)), '#a06030')
			return

		if moving is False:
			self.moveStatus = None
			# Leave the outcome of the last move up for a few seconds, or at
			# 5 Hz it would be replaced by 'ready' on the very next tick and
			# never actually be readable.
			if self.outcomeTicks > 0:
				self.outcomeTicks -= 1
				return
			self.setState('ready', '#306030')

	def positionIsChanging(self, position):
		# Only used when the record has no .DMOV. Compares against the reading
		# from the previous tick.
		previous = self.previousPosition
		self.previousPosition = position
		if previous is None:
			return False
		return abs(position - previous) > self.arrivalTolerance()

	def arrivalTolerance(self):
		# Close enough to call it arrived. Checks to see if the motor has a
		# number to try and hit, or 0.002 mm (2 um) is the fallback.
		#
		# In practice the fallback is what gets used. retry_deadband is NOT a
		# component of ophyd's EpicsMotor, nor of pcdsdevices' Newport or
		# anything it derives from, so this read raises AttributeError on a real
		# motor at UED too, not only on the test stubs. Kept because a device
		# that does map .RDBD would be worth honouring, but the 2 um is the
		# number that actually decides arrival. Matches erfFit.arrivalTolerance.
		try:
			value = float(self.device.retry_deadband.get())
			if value > 0:
				return max(value * 2, 1e-6)
		except Exception:
			pass
		return 0.002

	def statusFailure(self):
		# Returns a reason string if the status says the move failed, else ''.
		# A status that never completed is not treated as a failure, because
		# that is the unreliable case this no longer depends on.
		if self.moveStatus is None:
			return ''
		try:
			if not bool(self.moveStatus.done):
				return ''
			if bool(self.moveStatus.success):
				return ''
		except Exception:
			return ''
		return self.statusError()

	def statusError(self):
		try:
			if self.moveStatus is None:
				return 'unknown'
			error = getattr(self.moveStatus, 'exception', None)
			if callable(error):
				error = error()
			if error:
				return briefError(error)
		except Exception:
			pass
		return 'check the motor'


class MotorControlPanel(QGroupBox):

	# Jog controls for every distinct motor in the loaded config. Sits between
	# the scan list and the scan controls.

	def __init__(self, parent=None):
		super(MotorControlPanel, self).__init__('Motor control', parent)

		self.rows = []
		self.connected = False
		self.devices = MotorDevices()

		# Set by MainWindow, called whenever self.connected changes. Same
		# callback style as ConfigTree.editCallback.
		self.connectionChanged = None

		self.connectButton = QPushButton('Connect motors')
		self.connectButton.setEnabled(False)
		self.connectButton.setToolTip(
			'Connect to every motor in the loaded config and start reading\n'
			'positions. Does not run the scan.')
		self.connectButton.clicked.connect(self.connectMotors)

		self.stopAllButton = QPushButton('Stop all')
		self.stopAllButton.setEnabled(False)
		self.stopAllButton.setToolTip('Stop every connected motor now')
		self.stopAllButton.clicked.connect(self.stopAll)

		self.summaryLabel = QLabel('Load a config to list its motors.')
		self.summaryLabel.setStyleSheet('color: #555555;')

		header = QHBoxLayout()
		header.addWidget(self.connectButton)
		header.addWidget(self.stopAllButton)
		header.addWidget(self.summaryLabel, 1)

		self.rowHolder = QWidget()
		self.rowLayout = QVBoxLayout()
		self.rowLayout.setContentsMargins(0, 0, 0, 0)
		self.rowLayout.setSpacing(1)
		self.rowHolder.setLayout(self.rowLayout)

		# Scrolls, because a config with many motors should not push the scan
		# controls off the bottom of the window.
		self.scroll = QScrollArea()
		self.scroll.setWidget(self.rowHolder)
		self.scroll.setWidgetResizable(True)
		self.scroll.setMinimumHeight(90)
		self.scroll.setMaximumHeight(170)

		layout = QVBoxLayout()
		layout.addLayout(header)
		layout.addWidget(self.scroll)
		self.setLayout(layout)

	def showDoc(self, doc):
		# Rebuild the rows for a newly loaded config. Motors are de-duplicated
		# by PV, because the same motor usually appears in every scan of a
		# config, scanning in one and fixed in the others.
		for row in self.rows:
			self.rowLayout.removeWidget(row)
			row.setParent(None)
			row.deleteLater()
		self.rows = []
		self.connected = False

		seen = {}
		for scan in doc.scans:
			for motor in scan.motors:
				if motor.pv not in seen:
					seen[motor.pv] = motor

		for motor in seen.values():
			row = MotorRow(motor, self.devices)
			self.rows.append(row)
			self.rowLayout.addWidget(row)
		self.rowLayout.addStretch(1)

		self.stopAllButton.setEnabled(False)

		if self.rows:
			self.connectButton.setEnabled(True)
			self.summaryLabel.setText(
				'{} motor(s) found. Press Connect motors to read their positions.'.format(len(self.rows)))
		else:
			self.connectButton.setEnabled(False)
			self.summaryLabel.setText('No motors in this config.')

		# Rebuilding the rows dropped the connections, so Run has to go back to
		# needing Connect motors pressed again.
		self.announceConnection()

	def connectMotors(self):
		if not self.rows:
			return

		# Device construction connects to the records, so this can take a
		# moment per motor. It stays on the button press rather than the timer
		# so that it is a deliberate, one off cost.
		QApplication.setOverrideCursor(Qt.WaitCursor)
		try:
			for row in self.rows:
				row.connectMotor()
		finally:
			QApplication.restoreOverrideCursor()

		if self.devices.error:
			# Almost always a missing pcdsdevices. The rest of the GUI carries
			# on working, so say what happened rather than failing quietly.
			self.connected = False
			self.summaryLabel.setText('Motor control unavailable - {}'.format(self.devices.error))
			self.summaryLabel.setStyleSheet('color: #a03030;')
			self.announceConnection()
			return

		self.connected = True
		self.connectButton.setText('Reconnect motors')
		self.stopAllButton.setEnabled(True)
		self.summaryLabel.setStyleSheet('color: #555555;')
		self.summaryLabel.setText('Connecting to {} motor(s)...'.format(len(self.rows)))
		self.refresh()
		self.announceConnection()

	def announceConnection(self):
		if self.connectionChanged is not None:
			self.connectionChanged()

	def setJoggingEnabled(self, enabled):
		# Locked out while a scan runs. The scan drives the same device objects
		# these rows do, so a jog mid scan would move a motor out from under the
		# sweep and the data would be taken somewhere other than where the csv
		# says. Stop all stays live on purpose: it is the hardware stop.
		for row in self.rows:
			row.setJoggingEnabled(enabled)

	def stopAll(self):
		for row in self.rows:
			row.onStop()
		self.summaryLabel.setText('Stop sent to {} motor(s)'.format(len(self.rows)))

	def refresh(self):
		if not self.connected:
			return
		live = 0
		moving = 0
		for row in self.rows:
			if row.refresh():
				live += 1
				if row.isMoving() is True:
					moving += 1
		text = '{} of {} motor(s) connected'.format(live, len(self.rows))
		if moving:
			text += ', {} moving'.format(moving)
		self.summaryLabel.setText(text)


def buildScanList(doc, module, devices):
	# Turns the ConfigDoc on screen into the list of erfFit.scan objects
	# runConfig takes. Returns (scanList, error).
	#
	# Built from the specs' CURRENT values rather than by handing runConfig the
	# config path, so edits made in the tree are what actually run. The user is
	# not asked to save first: the edited values go straight into the scan and
	# the unsaved badge in the window is what says the file does not match.
	#
	# devices is MotorDevices.devices, which IS erfFit's master motorDict, so
	# addMotor hands back the very device objects the jog rows are polling
	# rather than connecting every motor a second time.
	scanList = []

	for spec in doc.scans:
		oneScan = module.scan()
		oneScan.numAvgs = spec.numAvgs

		for motor in spec.motors:
			try:
				# Goes through addMotor rather than scan(motorList=...), which
				# type checks for Newport and would reject anything else.
				oneScan.addMotor(motor.userInput(), devices)
			except Exception as e:
				return None, 'scan {}, {}: {}: {}'.format(
					spec.index, motor.pv, type(e).__name__, e)

		for dataPV in spec.dataPVs:
			oneScan.addDataPV(dataPV)

		scanList.append(oneScan)

	return scanList, None


class ScanWorker(QThread):

	# Runs erfFit.runConfig off the UI thread.
	#
	# This is the first thread in the program, and the rule that made the rest
	# of the GUI single threaded still holds inside it: NOTHING here touches a
	# widget. Progress and results leave by signal only, which Qt delivers on
	# the main thread, and the run() body talks to nothing but erfFit.
	#
	# runConfig blocks for the whole scan - it moves motors and sleeps 0.1s per
	# average - so it cannot be driven from the shared QTimer without freezing
	# the window for the length of the run.

	progress = pyqtSignal(int, int, int, float)	# scanNum, point, total, position
	scanStarted = pyqtSignal(int, str)			# scanNum, motor name
	finished = pyqtSignal(list, str, bool)		# save names, error, aborted

	def __init__(self, module, scanList, saveNames, summaryName, parent=None):
		super(ScanWorker, self).__init__(parent)

		self.module = module
		self.scanList = scanList
		self.saveNames = saveNames
		self.summaryName = summaryName

		# Plain bool rather than a lock. One thread sets it, one thread reads
		# it, and a missed read costs one more measured point.
		self.abortRequested = False

		self.scanNum = 0

	def requestAbort(self):
		self.abortRequested = True

	def shouldAbort(self):
		return self.abortRequested

	def report(self, point, total, position):
		# runConfig's report hook says which point of which sweep, but not which
		# scan. The first point of a sweep is always point 0, so that is the
		# scan boundary, and counting them recovers the scan number.
		if point == 0:
			self.scanNum += 1
			name = ''
			if self.scanNum < len(self.scanList):
				name = self.module.scanMotorName(
					self.scanList[self.scanNum], self.scanNum, askUser=False)
			self.scanStarted.emit(self.scanNum, name)

		self.progress.emit(self.scanNum, point, total, float(position))

	def run(self):
		error = ''
		aborted = False

		# Counted up by report(), which fires before the first move of a sweep.
		self.scanNum = -1

		try:
			# The whole list goes in one call rather than one call per scan.
			#
			# runConfig accumulates the summary rows across scans and rewrites
			# the summary file after each one, so calling it per scan would make
			# every call start a fresh row list and leave a summary holding only
			# the last scan. Its own loop already checks shouldAbort at the top
			# of each scan, so aborting between scans does not need the caller.
			#
			# plotRun=False is not optional. erfFit imports matplotlib.pyplot at
			# module scope and the backend here is QtAgg, so plotERF's
			# plt.show() would build Qt widgets on this thread - a hard crash.
			# ResultsWindow does the plotting instead, on the main thread, from
			# the csv files this writes.
			self.module.runConfig(self.scanList,
				saveRun=True,
				saveNames=list(self.saveNames),
				summaryName=self.summaryName,
				plotRun=False,
				askUser=False,
				shouldAbort=self.shouldAbort,
				report=self.report)

		except self.module.ScanAborted as e:
			# Distinct from a failure: the user asked for this.
			aborted = True
			error = str(e)

		except Exception as e:
			error = '{}: {}'.format(type(e).__name__, e)

		# Only the files that actually got written, so an abort partway through
		# still plots the scans that finished and does not try to read a csv
		# that was never created.
		written = [n for n in self.saveNames if os.path.isfile(n)]

		self.finished.emit(written, error, aborted)


class ScanControl(QGroupBox):

	# Run, abort and progress for a scan, plus opening a saved csv.
	#
	# The buttons only drive MainWindow slots; the thread and the erfFit module
	# live there, because MainWindow owns the config and the motor devices.

	def __init__(self, parent=None):
		super(ScanControl, self).__init__('Scan control', parent)

		self.runButton = QPushButton('Run scan')
		self.runButton.setEnabled(False)
		self.runButton.setToolTip('Connect the motors first, then run every scan in the '
			'loaded config and open the results when it finishes.')

		self.abortButton = QPushButton('Abort')
		self.abortButton.setEnabled(False)
		self.abortButton.setToolTip('Stop after the point being measured. Motors are left '
			'where they are rather than being sent back.')

		self.plotButton = QPushButton('Plot saved scan...')
		self.plotButton.setToolTip('Open a saved knife edge csv and show the data, the erf fit '
			'and the fit parameters. This is the window a finished scan opens.')

		self.progress = QProgressBar()
		self.progress.setMinimum(0)
		self.progress.setMaximum(1)
		self.progress.setValue(0)

		buttons = QHBoxLayout()
		buttons.addWidget(self.runButton)
		buttons.addWidget(self.abortButton)
		buttons.addWidget(self.plotButton)
		buttons.addWidget(self.progress, 1)

		self.statusLabel = QLabel('Load a config and connect its motors to run a scan.')
		self.statusLabel.setStyleSheet('color: #555555;')

		layout = QVBoxLayout()
		layout.addLayout(buttons)
		layout.addWidget(self.statusLabel)
		self.setLayout(layout)

	def setStatus(self, text, color='#555555'):
		self.statusLabel.setText(text)
		self.statusLabel.setStyleSheet('color: {};'.format(color))

	def setRunning(self, running):
		self.runButton.setEnabled(not running)
		self.abortButton.setEnabled(running)
		self.plotButton.setEnabled(not running)
		if not running:
			self.progress.setMaximum(1)
			self.progress.setValue(0)


class ResultsWindow(QDialog):

	# Separate window with the collected points, the erf fit curve and the fit
	# parameters. This is what a finished scan will pop up. It is a QDialog but
	# non modal, so it can sit beside the main window while you keep working.
	#
	# Deliberately given no parent widget and the Qt.Window type, which is what
	# lets the main window come back in front of it. A parented top level gets a
	# transientParent on its QWindow and window managers keep such a window
	# above its parent for ever, so clicking the main window raised nothing and
	# the results sat on top of the controls. Qt.Window also replaces the Dialog
	# type hint, which is the other half of the same behaviour and additionally
	# earns the window its own taskbar entry. The cost is that nothing owns it:
	# MainWindow has to keep the reference, which it already did, and close it
	# in its own closeEvent or the process would outlive the main window.

	def __init__(self, results):
		super(ResultsWindow, self).__init__(None)

		self.setWindowFlags(Qt.Window)
		self.setWindowTitle('Knife edge scan results')
		self.setModal(False)
		self.resize(1000, 560)

		self.results = [r for r in results if r is not None]

		# One plot per data set rather than one shared axis. An H and V pair is
		# taken on two different motors, so their positions have no reason to
		# overlap: putting zongH (13.5 to 15.5) and zongV (9 to 10.5) on a
		# common x axis squeezes both curves into the ends of the plot.
		self.plotLayout = pg.GraphicsLayoutWidget()
		self.plotLayout.setBackground('w')
		self.plots = []

		self.paramTable = QTableWidget()
		self.paramTable.setColumnCount(2)
		self.paramTable.setHorizontalHeaderLabels(['Parameter', 'Value'])
		self.paramTable.verticalHeader().hide()
		self.paramTable.setMinimumWidth(300)
		self.paramTable.setMaximumWidth(380)
		# A scan label long enough to overflow is cut with an ellipsis instead of
		# widening the column. The full text is on the row's tooltip, and the
		# plot title above carries it too.
		self.paramTable.setTextElideMode(Qt.ElideRight)
		self.paramTable.setWordWrap(False)

		closeButton = QPushButton('Close')
		closeButton.clicked.connect(self.close)

		buttons = QHBoxLayout()
		buttons.addStretch(1)
		buttons.addWidget(closeButton)

		splitter = QSplitter(Qt.Horizontal)
		splitter.addWidget(self.plotLayout)
		splitter.addWidget(self.paramTable)
		splitter.setSizes([640, 360])

		layout = QVBoxLayout()
		layout.addWidget(splitter, 1)
		layout.addLayout(buttons)
		self.setLayout(layout)

		self.populate()

	def populate(self):
		colors = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e']

		rows = []
		for index, result in enumerate(self.results):
			color = colors[index % len(colors)]

			plot = self.plotLayout.addPlot(row=index, col=0)
			plot.showGrid(x=True, y=True, alpha=0.3)
			plot.setLabel('bottom', 'Knife position (mm)')
			plot.setLabel('left', 'Amplitude (arb.)')
			# Bottom left, where an erf curve's low plateau leaves room.
			plot.addLegend(offset=(10, -10))
			self.plots.append(plot)

			# Truncated for the same reason the table's name row is: a long scan
			# label set the title's width, which is a minimum width for the whole
			# plot item, so the plot grew past the viewport and took the right
			# hand end of the curve and the x axis label off screen with it. 28
			# is the longest that still fits the default splitter position once
			# the 2w and FWHM values are appended. The untruncated name is on the
			# plot's tooltip and on the table's name row.
			label = elideText(result.label, 28)
			plot.setToolTip(result.label)
			if result.fitOK:
				plot.setTitle('{}   2w = {} mm, FWHM = {} mm'.format(
					label,
					erfFitData.fmtValue(result.twoW()),
					erfFitData.fmtValue(result.fwhm())))
			else:
				plot.setTitle('{}   fit failed'.format(label))

			plot.plot(result.x, result.y, pen=None, symbol='o',
				symbolSize=7, symbolBrush=color, symbolPen=color, name='data')

			if result.fitOK:
				plot.plot(result.fitX, result.fitY,
					pen=pg.mkPen(color, width=2), name='erf fit')

			if len(self.results) > 1:
				rows.append((result.label, '', True))
			for name, value in result.parameterRows():
				rows.append((name, value, False))
			if index < len(self.results) - 1:
				rows.append(('', '', False))

		self.paramTable.setRowCount(len(rows))
		for rowNum, (name, value, isHeader) in enumerate(rows):
			nameItem = QTableWidgetItem(name)
			if isHeader:
				font = nameItem.font()
				font.setBold(True)
				nameItem.setFont(font)
				nameItem.setBackground(QBrush(COLOR_HEADER))
				nameItem.setToolTip(name)
			self.paramTable.setItem(rowNum, 0, nameItem)

			if isHeader:
				# The scan name spans both columns. Left in column 0 it decided
				# the column width for every parameter row below it, and a long
				# motor name pushed the values out of sight behind a horizontal
				# scrollbar to fill a cell that was blank anyway.
				self.paramTable.setSpan(rowNum, 0, 1, 2)
			else:
				self.paramTable.setItem(rowNum, 1, QTableWidgetItem(value))

		# Sized from the parameter names only. resizeColumnsToContents measures
		# the spanned scan name rows too, which is the whole problem, so the
		# width is measured here from just the rows that use one column.
		metrics = self.paramTable.fontMetrics()
		widest = metrics.width('Parameter')
		for name, _, isHeader in rows:
			if not isHeader:
				widest = max(widest, metrics.width(name))
		self.paramTable.setColumnWidth(0, widest + 16)
		# Whatever is left goes to the values, so neither column needs a
		# scrollbar at the window's default width.
		self.paramTable.horizontalHeader().setSectionResizeMode(
			1, QHeaderView.Stretch)

		# Taller window when stacking more than one plot.
		if len(self.results) > 1:
			self.resize(1000, min(360 * len(self.results), 900))


class MainWindow(QMainWindow):

	def __init__(self, parent=None):
		super(MainWindow, self).__init__(parent)

		self.setWindowTitle('erfFit GUI')
		self.resize(1400, 780)
		self.setMinimumSize(1000, 600)

		self.configPath = None
		self.doc = None
		self.polling = False
		self.scanWorker = None
		self.scanNames = []
		self.resultsWindow = None

		self.cameraPanel = CameraPanel()
		# Run needs connected motors, so the panel says when that changes rather
		# than being polled for it. Connecting to connectButton directly would
		# run the slots in connection order and read self.connected before
		# connectMotors had set it.
		self.motorPanel = MotorControlPanel()
		self.motorPanel.connectionChanged = self.updateRunState

		self.loadButton = QPushButton('Load config...')
		self.loadButton.clicked.connect(self.onLoadConfig)

		self.reloadButton = QPushButton('Reload')
		self.reloadButton.setEnabled(False)
		self.reloadButton.setToolTip('Re-read the same file, for when you have just edited it.')
		self.reloadButton.clicked.connect(self.onReload)

		self.saveButton = QPushButton('Save config...')
		self.saveButton.setEnabled(False)
		self.saveButton.setToolTip('Write the current parameters to a config file.')
		self.saveButton.clicked.connect(self.onSaveConfig)

		self.revertButton = QPushButton('Revert edits')
		self.revertButton.setEnabled(False)
		self.revertButton.setToolTip('Put every edited value back to what the file said.')
		self.revertButton.clicked.connect(self.onRevertEdits)

		self.fileLabel = QLabel('No config loaded')

		self.editedLabel = QLabel('')
		self.editedLabel.setStyleSheet(
			'color: #144696; background-color: #d7e8ff; border: 1px solid #93b4de; padding: 2px 6px;')
		self.editedLabel.hide()

		# Sits beside the blue edited badge. A run uses the values on screen, so
		# once anything is edited it matters that the file does not have them.
		self.unsavedLabel = QLabel('Not saved')
		self.unsavedLabel.setStyleSheet(STYLE_UNSAVED)
		self.unsavedLabel.setToolTip('The scan will run with the edited values shown here, '
			'not with what the config file on disk says. Save the config to keep them.')
		self.unsavedLabel.hide()

		fileRow = QHBoxLayout()
		fileRow.addWidget(self.loadButton)
		fileRow.addWidget(self.reloadButton)
		fileRow.addWidget(self.saveButton)
		fileRow.addWidget(self.revertButton)
		fileRow.addWidget(self.fileLabel, 1)

		# The badges get their own row. The button row above is already full
		# enough to clip its own labels at the default window width, and these
		# two are the warning that a run will not use the saved file, so they
		# are the last thing that should be squeezed off the edge.
		badgeRow = QHBoxLayout()
		badgeRow.setContentsMargins(0, 0, 0, 0)
		badgeRow.addWidget(self.editedLabel)
		badgeRow.addWidget(self.unsavedLabel)
		badgeRow.addStretch(1)

		self.configTree = ConfigTree()
		self.configTree.editCallback = self.onConfigEdited

		self.warningPane = QPlainTextEdit()
		self.warningPane.setReadOnly(True)
		self.warningPane.setMaximumHeight(90)
		self.warningPane.hide()

		self.scanControl = ScanControl()
		self.scanControl.plotButton.clicked.connect(self.onPlotSavedScan)
		self.scanControl.runButton.clicked.connect(self.onRunScan)
		self.scanControl.abortButton.clicked.connect(self.onAbortScan)

		rightLayout = QVBoxLayout()
		rightLayout.addLayout(fileRow)
		rightLayout.addLayout(badgeRow)
		rightLayout.addWidget(self.configTree, 1)
		rightLayout.addWidget(self.warningPane)
		rightLayout.addWidget(self.motorPanel)
		rightLayout.addWidget(self.scanControl)
		rightPanel = QWidget()
		rightPanel.setLayout(rightLayout)

		splitter = QSplitter(Qt.Horizontal)
		splitter.addWidget(self.cameraPanel)
		splitter.addWidget(rightPanel)
		splitter.setSizes([640, 760])
		self.setCentralWidget(splitter)

		self.statusBar().showMessage('Ready')

		# One timer on the main thread. Every EPICS read is guarded by a
		# connected check first, so a dead network costs microseconds per tick.
		# If a live camera ever makes this feel sluggish, the fix is to put
		# auto_monitor=True on the active feed and have the callback only store
		# the latest array for the timer to draw. Such a callback must never
		# touch a widget directly.
		self.timer = QTimer(self)
		self.timer.timeout.connect(self.onTick)
		self.timer.start(CAMERA_INTERVAL)

	def onTick(self):
		if self.polling:
			return
		self.polling = True
		try:
			self.cameraPanel.refresh()
			self.motorPanel.refresh()
		finally:
			self.polling = False

	def onLoadConfig(self):
		startDir = os.path.dirname(self.configPath) if self.configPath \
			else erfFitPaths.configDir()
		path, _ = QFileDialog.getOpenFileName(self, 'Load scan config', startDir,
			'Config files (*.txt);;All files (*)')
		if path:
			self.loadConfig(path)

	def onReload(self):
		if self.configPath:
			self.loadConfig(self.configPath)

	def loadConfig(self, path):
		doc = erfFitConfig.parseConfigFile(path)

		self.configPath = path
		self.doc = doc
		self.reloadButton.setEnabled(True)
		self.fileLabel.setText(os.path.basename(path))
		self.fileLabel.setToolTip(path)

		self.configTree.showDoc(doc)
		self.motorPanel.showDoc(doc)

		messages = []
		if doc.error:
			messages.append('ERROR: ' + doc.error)
		messages.extend(doc.warnings)

		if messages:
			self.warningPane.setPlainText('\n'.join(messages))
			self.warningPane.show()
		else:
			self.warningPane.clear()
			self.warningPane.hide()

		self.updateEditState()

		if doc.error:
			self.statusBar().showMessage('{}: {}'.format(os.path.basename(path), doc.error))
		else:
			self.statusBar().showMessage('Loaded {} scan(s) from {}. Double click a motor or '
				'data PV to edit it.'.format(len(doc.scans), os.path.basename(path)), 6000)

	def onConfigEdited(self):
		# An edit can rename a motor or change its PV, so the jog rows are
		# rebuilt to match. That drops any motor connections, which is the
		# honest behaviour: the row no longer refers to the same motor.
		self.motorPanel.showDoc(self.doc)
		self.updateEditState()

	def updateEditState(self):
		doc = self.doc
		hasScans = doc is not None and bool(doc.scans)
		edited = hasScans and doc.isEdited()

		self.saveButton.setEnabled(hasScans)
		self.revertButton.setEnabled(edited)

		if edited:
			self.editedLabel.setText('Edited: ' + doc.editSummary())
			self.editedLabel.show()
			self.unsavedLabel.show()
			self.setWindowTitle('erfFit GUI - {} (edited)'.format(os.path.basename(self.configPath or '')))
		else:
			self.editedLabel.hide()
			self.unsavedLabel.hide()
			if self.configPath:
				self.setWindowTitle('erfFit GUI - {}'.format(os.path.basename(self.configPath)))
			else:
				self.setWindowTitle('erfFit GUI')

		# Problems block a save, so say which rather than failing at save time.
		if hasScans:
			problems = doc.validate()
			if problems:
				self.saveButton.setToolTip('Fix these first:\n  ' + '\n  '.join(problems))
			else:
				self.saveButton.setToolTip('Write the current parameters to a config file.')

		self.updateRunState()

	def updateRunState(self):
		# Run needs a valid config AND connected motors. The same problems that
		# block a save block a run, because erfFit would raise on them anyway -
		# a scan with no scanning motor, or two, cannot be run at all.
		if self.scanWorker is not None and self.scanWorker.isRunning():
			return

		doc = self.doc
		hasScans = doc is not None and bool(doc.scans)
		problems = doc.validate() if hasScans else ['no config loaded']
		connected = self.motorPanel.connected

		canRun = hasScans and not problems and connected
		self.scanControl.runButton.setEnabled(canRun)

		if not hasScans:
			self.scanControl.setStatus('Load a config and connect its motors to run a scan.')
		elif problems:
			self.scanControl.setStatus('Cannot run: ' + problems[0], '#a03030')
			self.scanControl.runButton.setToolTip('Fix these first:\n  ' + '\n  '.join(problems))
		elif not connected:
			self.scanControl.setStatus('Press Connect motors before running a scan.')
			self.scanControl.runButton.setToolTip('Connect the motors first.')
		else:
			self.scanControl.setStatus('Ready to run {} scan(s).'.format(len(doc.scans)))
			self.scanControl.runButton.setToolTip('Run every scan in the loaded config and '
				'open the results when it finishes.')

	def onRevertEdits(self):
		if self.doc is None:
			return
		self.doc.revertEdits()
		self.configTree.rebuild()
		self.motorPanel.showDoc(self.doc)
		self.updateEditState()
		self.statusBar().showMessage('Reverted to the values in the file', 4000)

	def onSaveConfig(self):
		if self.doc is None or not self.doc.scans:
			return

		problems = self.doc.validate()
		if problems:
			QMessageBox.warning(self, 'Cannot save yet',
				'These need fixing before the config can be saved:\n\n  ' + '\n  '.join(problems))
			return

		startDir = self.configPath or erfFitPaths.configDir()
		path, _ = QFileDialog.getSaveFileName(self, 'Save scan config', startDir,
			'Config files (*.txt);;All files (*)')
		if not path:
			return

		if not path.endswith('.txt'):
			path += '.txt'

		# Overwriting the file you loaded from is a normal thing to want here,
		# but it should be a deliberate answer rather than a side effect.
		if os.path.abspath(path) == os.path.abspath(self.configPath or ''):
			answer = QMessageBox.question(self, 'Overwrite original?',
				'Overwrite {} with the edited values?'.format(os.path.basename(path)),
				QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
			if answer != QMessageBox.Yes:
				return

		error = erfFitConfig.saveConfigFile(self.doc, path)
		if error:
			QMessageBox.warning(self, 'Could not save', error)
			return

		# Reload from what was just written, so the display matches the file on
		# disk and the edited markers clear.
		self.loadConfig(path)
		self.statusBar().showMessage('Saved to {}'.format(path), 6000)

	def onRunScan(self):
		# Starts the worker. Everything that can be checked without moving a
		# motor is checked here, on the main thread, so a bad config fails in a
		# dialog rather than partway through a run.
		if self.doc is None or not self.doc.scans:
			return

		if self.scanWorker is not None and self.scanWorker.isRunning():
			return

		problems = self.doc.validate()
		if problems:
			QMessageBox.warning(self, 'Cannot run this config',
				'These need fixing first:\n\n  ' + '\n  '.join(problems))
			return

		module = self.motorPanel.devices.load()
		if module is None:
			QMessageBox.warning(self, 'Cannot run a scan',
				'erfFit could not be imported:\n\n{}'.format(self.motorPanel.devices.error))
			return

		baseName, ok = QInputDialog.getText(self, 'Run scan',
			'Base filename for this run:\n\nOne csv per scan is written, named for the '
			'scanning motor, plus a summary.')
		if not ok or not baseName.strip():
			return

		# The scan objects are built from the edited values on screen, sharing
		# the already connected devices.
		scanList, error = buildScanList(self.doc, module, self.motorPanel.devices.devices)
		if error:
			QMessageBox.warning(self, 'Cannot build the scan', error)
			return

		# Derives the filenames and checks the save directory exists BEFORE
		# anything moves, so a bad path is not discovered after the first sweep.
		try:
			saveNames, summaryName = module.makeSaveNames(scanList, baseName.strip(),
				askUser=False, directory=erfFitPaths.dataDir())
		except Exception as e:
			QMessageBox.warning(self, 'Cannot work out the filenames',
				'{}: {}'.format(type(e).__name__, e))
			return

		clashes = [n for n in saveNames + [summaryName] if os.path.exists(n)]
		if clashes:
			answer = QMessageBox.question(self, 'Overwrite existing data?',
				'These files already exist and will be overwritten:\n\n  {}\n\nContinue?'.format(
					'\n  '.join([os.path.basename(c) for c in clashes])),
				QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
			if answer != QMessageBox.Yes:
				return

		self.scanNames = saveNames

		self.scanWorker = ScanWorker(module, scanList, saveNames, summaryName, self)
		self.scanWorker.progress.connect(self.onScanProgress)
		self.scanWorker.scanStarted.connect(self.onScanStarted)
		self.scanWorker.finished.connect(self.onScanFinished)

		# Jogging a motor the scan is driving would fight the scan for it, and
		# both go through the same device object. Stop all stays live.
		self.motorPanel.setJoggingEnabled(False)
		self.scanControl.setRunning(True)
		self.scanControl.setStatus('Starting {} scan(s)...'.format(len(scanList)))
		self.statusBar().showMessage('Scan running')

		self.scanWorker.start()

	def onAbortScan(self):
		if self.scanWorker is None or not self.scanWorker.isRunning():
			return

		# Only sets a flag. The run stops at the next check, between points or
		# between averages, rather than being killed mid move.
		self.scanWorker.requestAbort()
		self.scanControl.abortButton.setEnabled(False)
		self.scanControl.setStatus('Abort requested, finishing the current point...', '#a05000')

	def onScanStarted(self, scanNum, motorName):
		label = motorName if motorName else 'scan {}'.format(scanNum + 1)
		self.scanControl.setStatus('Scan {} of {}: sweeping {}'.format(
			scanNum + 1, len(self.scanNames), label))

	def onScanProgress(self, scanNum, point, total, position):
		# Delivered on the main thread by Qt, so touching widgets here is safe.
		self.scanControl.progress.setMaximum(total)
		self.scanControl.progress.setValue(point)
		self.scanControl.progress.setFormat('scan {} of {}: point %v of %m'.format(
			scanNum + 1, len(self.scanNames)))
		self.statusBar().showMessage('Scan {}, point {} of {}, moving to {}'.format(
			scanNum + 1, point + 1, total, erfFitConfig.fmtNum(position)))

	def onScanFinished(self, written, error, aborted):
		self.scanControl.setRunning(False)
		self.motorPanel.setJoggingEnabled(True)
		self.scanWorker = None
		self.updateRunState()

		if error and not aborted:
			self.scanControl.setStatus('Scan failed: ' + error, '#a03030')
			self.statusBar().showMessage('Scan failed')
			QMessageBox.warning(self, 'Scan failed',
				'{}\n\nAny data collected before the failure has been saved.'.format(error))
		elif aborted:
			self.scanControl.setStatus('Scan aborted. {} file(s) saved.'.format(len(written)),
				'#a05000')
			self.statusBar().showMessage('Scan aborted')
		else:
			self.scanControl.setStatus('Scan finished. {} file(s) saved.'.format(len(written)))
			self.statusBar().showMessage('Scan finished')

		# The fits come from the saved csvs rather than from runConfig, which
		# returns None on purpose so a CLI run does not fill the shell with
		# output. Re-fitting here is cheap and erfFitData uses the same fit
		# function and starting guess, so the numbers match what erfFit printed.
		if written:
			self.showResults([erfFitData.loadAndFit(path) for path in written])

	def onPlotSavedScan(self):
		# Opens saved knife edge csv files and shows them in the results window.
		# Selecting the H and V file of a pair plots both together.
		paths, _ = QFileDialog.getOpenFileNames(self, 'Open saved knife edge scan',
			erfFitPaths.dataDir(), 'Scan data (*.csv);;All files (*)')
		if not paths:
			return

		results = [erfFitData.loadAndFit(path) for path in paths]
		self.showResults(results)

	def showResults(self, results):
		# Shown as a separate window, which is also how a finished scan will
		# present its data. Keep a reference, or Qt will garbage collect it the
		# moment this method returns: the window is parentless so that the main
		# window can be raised over it, which means this attribute is the only
		# thing owning it.
		failed = [r for r in results if not r.fitOK]

		# Plotting a second time replaces the first window rather than leaving a
		# stack of them behind. Closing before dropping the reference, because
		# the parentless window would otherwise stay on screen with nothing
		# holding it but Qt's top level list.
		if self.resultsWindow is not None:
			self.resultsWindow.close()

		self.resultsWindow = ResultsWindow(results)
		self.resultsWindow.show()

		if failed:
			names = ', '.join([r.label for r in failed])
			self.statusBar().showMessage('Fit failed for {}, raw data still plotted'.format(names), 6000)
		else:
			self.statusBar().showMessage('Plotted {} scan(s)'.format(len(results)), 4000)

	def closeEvent(self, event):
		# A running scan is moving motors, so closing the window has to be a
		# deliberate answer rather than leaving a thread driving hardware with
		# nothing watching it.
		if self.scanWorker is not None and self.scanWorker.isRunning():
			answer = QMessageBox.question(self, 'Scan still running',
				'A scan is running. Abort it and close?',
				QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
			if answer != QMessageBox.Yes:
				event.ignore()
				return

			self.scanWorker.requestAbort()
			# Waits for the abort to be noticed, which happens at the next point
			# or average rather than instantly.
			QApplication.setOverrideCursor(Qt.WaitCursor)
			try:
				self.scanWorker.wait(10000)
			finally:
				QApplication.restoreOverrideCursor()

		# Parentless, so Qt does not take it down with this window and the last
		# window closed rule would keep the process alive behind a plot.
		if self.resultsWindow is not None:
			self.resultsWindow.close()

		super(MainWindow, self).closeEvent(event)

	def hideEvent(self, event):
		# No reason to pull frames into a window nobody can see. The scan is a
		# thread of its own, so it carries on regardless.
		self.timer.stop()
		super(MainWindow, self).hideEvent(event)

	def showEvent(self, event):
		if not self.timer.isActive():
			self.timer.start(CAMERA_INTERVAL)
		super(MainWindow, self).showEvent(event)


def main():
	# Printed so it is obvious which directories this session is using, which
	# is the first thing to check if a file dialog opens somewhere unexpected.
	print('erfFit GUI directories:')
	print(erfFitPaths.describe())

	app = QApplication(sys.argv)
	window = MainWindow()
	if len(sys.argv) > 1:
		window.loadConfig(sys.argv[1])
	window.show()
	return app.exec_()


if __name__ == '__main__':
	sys.exit(main())
