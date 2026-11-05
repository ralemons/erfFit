# Camera markers, display only. Checks the prefix surgery that finds the cross
# PVs, which crosses MarkerFeed decides to draw, the ROI origin subtraction, and
# that the panel keeps the scene tidy as markers come and go.
# Randy Lemons

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt5.QtWidgets import QApplication
import erfFit_GUI as G


class StubPV:
	# Enough of pyepics' PV for MarkerFeed: a connected flag and a get(). The
	# real one is never built, because these tests run where there is no EPICS.
	def __init__(self, value=None, connected=True):
		self.value = value
		self.connected = connected
		self.gets = 0

	def get(self, timeout=None, as_string=False):
		self.gets += 1
		if self.value is None:
			return None
		if as_string:
			return str(self.value)
		return self.value


def makeFeed(slots, roi=None):
	# slots is a list of dicts of the keyword values for one cross, so a test
	# says only what it cares about. A MarkerFeed is built without __init__ so
	# no PV is constructed. roi is (x, y) of the ROI origin, or None for a
	# camera with no ROI plugin.
	feed = G.MarkerFeed.__new__(G.MarkerFeed)
	feed.base = 'UED:GIGE:04:'
	feed.slots = []
	for index, slot in enumerate(slots):
		feed.slots.append({
			'number': index + 1,
			'x': slot.get('x', StubPV(None, connected=False)),
			'y': slot.get('y', StubPV(None, connected=False)),
		})
	if roi is None:
		feed.roiX = StubPV(None, connected=False)
		feed.roiY = StubPV(None, connected=False)
	else:
		feed.roiX = StubPV(roi[0])
		feed.roiY = StubPV(roi[1])
	return feed


def main():
	app = QApplication(sys.argv)
	bad = 0

	def check(label, got, expect):
		nonlocal bad
		ok = (expect(got) if callable(expect) else got == expect)
		print('  {:<48} {:<34} {}'.format(label, repr(got), 'OK' if ok else 'BAD'))
		if not ok:
			bad += 1

	print('\n=== the cross PVs are on the camera base, not the image plugin ===')
	check('IMAGE1 is stripped', G.cameraBase('UED:GIGE:04:IMAGE1:'), 'UED:GIGE:04:')
	check('lowercase image1 too', G.cameraBase('ASPS04:image1:'), 'ASPS04:')
	check('a bare camera base is unchanged', G.cameraBase('UED:GIGE:04:'), 'UED:GIGE:04:')
	check('a non image plugin is left alone', G.cameraBase('UED:GIGE:04:ROI1:'), 'UED:GIGE:04:ROI1:')
	check('empty stays empty', G.cameraBase(''), '')

	print('\n=== the PV names match the ones recorded at UED ===')
	# UED:GIGE:03:Cross2X is in a recorded caget dump reading 1080, so the
	# generated name has to come out in exactly that form.
	feed = G.MarkerFeed('UED:GIGE:03:IMAGE1:')
	check('first cross X', feed.slots[0]['x'].pvname, 'UED:GIGE:03:Cross1X')
	check('the one UED actually sets', feed.slots[1]['x'].pvname, 'UED:GIGE:03:Cross2X')
	check('and its Y', feed.slots[1]['y'].pvname, 'UED:GIGE:03:Cross2Y')
	check('four crosses', len(feed.slots), 4)
	check('ROI origin X', feed.roiX.pvname, 'UED:GIGE:03:ROI1:MinX')
	check('not the NDOverlay convention',
		all('Over' not in s['x'].pvname for s in feed.slots), True)

	print('\n=== which crosses get drawn ===')
	feed = makeFeed([
		{'x': StubPV(100.0), 'y': StubPV(50.0)},
		# Unset. CamViewer has no EPICS enable flag, so 0,0 is how an unplaced
		# cross reads and it must not be drawn in the corner.
		{'x': StubPV(0), 'y': StubPV(0)},
		{'x': StubPV(300.0), 'y': StubPV(90.0)},
	])
	markers = feed.readMarkers()
	check('a placed cross is drawn', markers[0], (100.0, 50.0, 1))
	check('0,0 is treated as unplaced', len(markers), 2)
	check('carries its cross number', markers[1], (300.0, 90.0, 3))

	print('\n=== a cross on one axis only is still a cross ===')
	feed = makeFeed([{'x': StubPV(0), 'y': StubPV(40.0)}])
	check('x = 0 with a real y is kept', feed.readMarkers(), [(0.0, 40.0, 1)])

	print('\n=== a camera with no crosses set shows no markers ===')
	feed = makeFeed([{}, {}, {}])
	check('all disconnected, nothing drawn', feed.readMarkers(), [])

	print('\n=== partial and bad reads never raise ===')
	feed = makeFeed([
		# Connected but returning None, which is what a timing out get does.
		{'x': StubPV(None), 'y': StubPV(None)},
		# X connected, Y not. Half a position is not a position.
		{'x': StubPV(10.0), 'y': StubPV(20.0, connected=False)},
		{'x': StubPV(5.0), 'y': StubPV(6.0)},
	])
	markers = feed.readMarkers()
	check('only the usable one survives', markers, [(5.0, 6.0, 3)])

	print('\n=== integers and numpy scalars come out as floats ===')
	feed = makeFeed([{'x': StubPV(320), 'y': StubPV(240)}])
	markers = feed.readMarkers()
	check('x is a float', isinstance(markers[0][0], float), True)
	check('value preserved', markers[0][:2], (320.0, 240.0))

	print('\n=== the ROI origin is subtracted ===')
	# The crosses are full sensor pixels but the frame on screen starts at the
	# ROI corner. UED:GIGE:06 really is cropped to 281,295 in a recorded caget,
	# so getting this wrong misplaces the marker by that much.
	feed = makeFeed([{'x': StubPV(400.0), 'y': StubPV(350.0)}], roi=(281, 295))
	check('cross lands in frame coordinates', feed.readMarkers(),
		[(119.0, 55.0, 1)])

	feed = makeFeed([{'x': StubPV(400.0), 'y': StubPV(350.0)}], roi=(0, 0))
	check('an uncropped camera is unchanged', feed.readMarkers(),
		[(400.0, 350.0, 1)])

	feed = makeFeed([{'x': StubPV(400.0), 'y': StubPV(350.0)}], roi=None)
	check('no ROI plugin means no offset', feed.readMarkers(),
		[(400.0, 350.0, 1)])

	print('\n=== a bad ROI read does not move the markers ===')
	feed = makeFeed([{'x': StubPV(400.0), 'y': StubPV(350.0)}])
	feed.roiX = StubPV('rubbish')
	feed.roiY = StubPV(None)
	check('falls back to no offset', feed.readMarkers(), [(400.0, 350.0, 1)])

	print('\n=== a dead slot costs no gets ===')
	# A camera with no crosses at all should not pay a get per slot per tick.
	x = StubPV(None, connected=False)
	feed = makeFeed([{'x': x}])
	feed.readMarkers()
	check('no get on a disconnected cross', x.gets, 0)

	print('\n=== a sick IOC cannot freeze the camera tick ===')
	# Connected PVs that never answer are the dangerous case: each burns its
	# whole timeout, and 32 of them would stall the shared QTimer. Both the per
	# PV timeout and the overall budget are needed to bound it.
	class SlowPV:
		def __init__(self):
			self.connected = True

		def get(self, timeout=None, as_string=False):
			time.sleep(timeout if timeout else 0)
			return None

	feed = makeFeed([{'x': SlowPV(), 'y': SlowPV()} for _ in range(G.MARKER_SLOTS)])
	feed.roiX = SlowPV()
	feed.roiY = SlowPV()
	start = time.time()
	feed.readMarkers()
	elapsed = time.time() - start
	tick = G.CAMERA_INTERVAL
	check('bounded well inside one tick ({} ms)'.format(tick),
		elapsed < tick / 1000.0, True)

	print('\n=== a position that is not a number is skipped ===')
	class ValuePV:
		def __init__(self, value):
			self.connected = True
			self.value = value

		def get(self, timeout=None, as_string=False):
			return str(self.value) if as_string else self.value

	feed = makeFeed([
		# A waveform where a scalar was expected.
		{'x': ValuePV(np.array([1.0, 2.0])), 'y': ValuePV(3.0)},
		# A string that is not a number.
		{'x': ValuePV('notanumber'), 'y': ValuePV(3.0)},
		# numpy scalars, which is what pyepics actually hands back.
		{'x': ValuePV(np.float64(12.5)), 'y': ValuePV(np.int32(7))},
	])
	markers = feed.readMarkers()
	check('only the numeric one survives', markers, [(12.5, 7.0, 3)])

	print('\n=== the cross is a thin plus and nothing else ===')
	# pyqtgraph's '+' is the outline of a plus shaped polygon, so a pen traces
	# its whole 13 segment border and it reads as a thick hollow cross. Two bare
	# strokes is four path elements: moveTo, lineTo, moveTo, lineTo.
	symbol = G.markerSymbol()
	check('four path elements, not a polygon outline', symbol.elementCount(), 4)
	from pyqtgraph.graphicsItems.ScatterPlotItem import Symbols
	check("pyqtgraph's '+' really is the thick one",
		Symbols['+'].elementCount() > 4, True)
	bounds = symbol.boundingRect()
	check('spans the full normalised box, so size is honoured',
		(bounds.width(), bounds.height()), (1.0, 1.0))
	check('centred on the marker',
		(bounds.center().x(), bounds.center().y()), (0.0, 0.0))
	check('one pixel pen', G.MARKER_WIDTH, 1)
	check('same size as before', G.MARKER_SIZE, 24)

	print('\n=== CamViewer colour order ===')
	check('1 is blue', G.MARKER_COLORS[0], '#0000ff')
	check('2 is red', G.MARKER_COLORS[1], '#ff0000')
	check('3 is cyan', G.MARKER_COLORS[2], '#00ffff')
	check('4 is magenta', G.MARKER_COLORS[3], '#ff00ff')

	print('\n=== the panel draws what it is given ===')
	panel = G.CameraPanel()
	check('markers are on by default', panel.markersBox.isChecked(), True)
	check('one scatter item per colour', len(panel.markerItems), 4)
	check('every item uses the thin pen',
		all(i.opts['pen'].width() == G.MARKER_WIDTH for i in panel.markerItems), True)
	check('pen colours are the CamViewer order',
		[i.opts['pen'].color().name() for i in panel.markerItems], G.MARKER_COLORS)

	def drawn(panel):
		# (cross number -> point count) for whatever is currently on screen.
		out = {}
		for number, item in enumerate(panel.markerItems, start=G.MARKER_FIRST_SLOT):
			xs = item.getData()[0]
			if xs is not None and len(xs):
				out[number] = len(xs)
		return out

	check('nothing drawn before a frame', drawn(panel), {})

	print('\n=== colour follows the cross number, not the list order ===')
	# Only crosses 2 and 4 set. They must still come out red and magenta, which
	# is the whole reason for matching CamViewer.
	panel.drawMarkers([(30.0, 40.0, 2), (50.0, 60.0, 4)])
	check('two crosses, in slots 2 and 4', drawn(panel), {2: 1, 4: 1})
	check('cross 2 is at its pixel',
		(panel.markerItems[1].getData()[0][0], panel.markerItems[1].getData()[1][0]),
		(30.0, 40.0))
	check('nothing in the blue item', len(panel.markerItems[0].getData()[0] or []), 0)

	panel.drawMarkers([(1.0, 2.0, 1), (3.0, 4.0, 2), (5.0, 6.0, 3), (7.0, 8.0, 4)])
	check('all four', drawn(panel), {1: 1, 2: 1, 3: 1, 4: 1})

	panel.drawMarkers([(9.0, 9.0, 3)])
	check('back to one, in the cyan item', drawn(panel), {3: 1})

	panel.drawMarkers([])
	check('cleared', drawn(panel), {})

	print('\n=== a cross number beyond the colour list cycles ===')
	# Not expected, but an out of range index would be a crash rather than a
	# wrong colour, so it wraps.
	panel.drawMarkers([(1.0, 1.0, 5)])
	check('cross 5 lands back on the first colour', drawn(panel), {1: 1})

	print('\n=== turning markers off clears them at once ===')
	panel.drawMarkers([(10.0, 20.0, 1)])
	panel.markersBox.setChecked(False)
	check('off clears the crosses', drawn(panel), {})

	print('\n=== switching camera clears the previous markers ===')
	panel.markersBox.setChecked(True)
	panel.drawMarkers([(10.0, 20.0, 1)])
	# onCameraChanged calls refresh, which finds no EPICS here and falls through
	# to the placeholder. Either way the old markers must be gone.
	panel.cameraCombo.setCurrentIndex(1)
	check('cleared on camera change', drawn(panel), {})

	print('\n=== a marker never changes the auto range ===')
	# ignoreBounds, or a cross parked outside the frame would shrink the image
	# to make room for it.
	view = panel.imageView.getView()
	check('no marker item is in the bounds list',
		all(all(item is not m for m in panel.markerItems)
			for item in view.addedItems), True)

	print('\n=== markers are display only ===')
	# Nothing in this program writes an overlay position. The scatter items are
	# built without movable handles, and MarkerFeed only ever calls get().
	source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
		'..', 'erfFit_GUI.py')).read()
	marker = source[source.index('class MarkerFeed'):source.index('class CameraFeed')]
	check('MarkerFeed never calls put', '.put(' not in marker, True)
	check('MarkerFeed never uses caput', 'caput' not in marker, True)

	print('\n=== the orientation dropdown offers all eight and nothing else ===')
	check('eight orientations', len(G.ORIENTATIONS), 8)
	check('the first is the identity', G.ORIENTATIONS[0][1:], (False, 0))
	check('every mirror and turn combination exactly once',
		sorted((m, r) for _, m, r in G.ORIENTATIONS),
		sorted((m, r) for m in (False, True) for r in (0, 90, 180, 270)))
	check('the panel combo has them in that order',
		[panel.orientCombo.itemText(i) for i in range(panel.orientCombo.count())],
		[label for label, _, _ in G.ORIENTATIONS])
	check('starts untransformed', panel.currentOrientation(), (False, 0))

	print('\n=== a transformed pixel keeps its value ===')
	# Not a square image, so a quarter turn that forgot to swap the dimensions
	# cannot pass, and every pixel distinct so a point landing one off is
	# visible. 7 wide by 4 high, values 0..27.
	frame = np.arange(28).reshape(4, 7)
	for label, mirror, rotation in G.ORIENTATIONS:
		turned = G.orientImage(frame, mirror, rotation)
		height, width = frame.shape
		turns = G.quarterTurns(rotation)
		wantShape = (width, height) if turns % 2 else (height, width)
		ok = (turned.shape == wantShape)
		# Every pixel of the input, through orientPoint, must index the pixel of
		# the output that holds the same value. This is the whole claim being
		# made about the marker positions.
		for y in range(height):
			for x in range(width):
				px, py = G.orientPoint(x, y, width, height, mirror, rotation)
				if not (0 <= px < turned.shape[1] and 0 <= py < turned.shape[0]):
					ok = False
					break
				if turned[int(py), int(px)] != frame[y, x]:
					ok = False
					break
		check('{:<16} shape {} and every pixel'.format(label, wantShape), ok, True)

	print('\n=== the eight are distinct pictures ===')
	# If two orientations produced the same array one of them is mislabelled.
	seen = []
	for label, mirror, rotation in G.ORIENTATIONS:
		turned = G.orientImage(frame, mirror, rotation)
		seen.append(turned.tobytes() + str(turned.shape).encode())
	check('no two orientations are the same transform', len(set(seen)), 8)

	print('\n=== the labelled direction is clockwise ===')
	# A 2x2 with the corners named, turned 90 CW: the top left value has to end
	# up top right. Anticlockwise would put it bottom left.
	corners = np.array([[1, 2], [3, 4]])
	cw = G.orientImage(corners, False, 90)
	check('90 CW moves top left to top right', cw.tolist(), [[3, 1], [4, 2]])
	check('mirror reverses x only', G.orientImage(corners, True, 0).tolist(),
		[[2, 1], [4, 3]])
	# Mirror then 180 is a vertical mirror, which is the identity on x.
	check('mirror + 180 is a vertical flip',
		G.orientImage(corners, True, 180).tolist(), [[3, 4], [1, 2]])

	print('\n=== a colour frame keeps its colour axis ===')
	# The transform is written on the first two axes only, or an RGB frame comes
	# out with the channels rotated into the picture.
	color = np.arange(4 * 7 * 3).reshape(4, 7, 3)
	turned = G.orientImage(color, True, 90)
	check('shape is (width, height, 3)', turned.shape, (7, 4, 3))
	# Where orientPoint says a pixel went, all three of its channels must be,
	# in order. The index is taken from orientPoint rather than worked out by
	# hand so this cannot be made to pass by writing the wrong answer twice.
	px, py = G.orientPoint(6, 3, 7, 4, True, 90)
	check('a pixel keeps all three channels in order',
		turned[int(py), int(px)].tolist(), color[3, 6].tolist())
	check('and every channel of every pixel',
		all(turned[int(G.orientPoint(x, y, 7, 4, True, 90)[1]),
				int(G.orientPoint(x, y, 7, 4, True, 90)[0])].tolist()
			== color[y, x].tolist()
			for y in range(4) for x in range(7)), True)

	print('\n=== the oriented array is safe to hand to Qt ===')
	# A reversed slice or a rot90 is a negative or transposed stride view.
	for label, mirror, rotation in G.ORIENTATIONS:
		turned = G.orientImage(frame, mirror, rotation)
		if not turned.flags['C_CONTIGUOUS']:
			check('{} is contiguous'.format(label), False, True)
	check('all eight are contiguous',
		all(G.orientImage(frame, m, r).flags['C_CONTIGUOUS']
			for _, m, r in G.ORIENTATIONS), True)

	print('\n=== a rotation that is not a quarter turn cannot crash it ===')
	check('nearest quarter turn', G.quarterTurns(89), 1)
	check('a full turn is none', G.quarterTurns(360), 0)
	check('rubbish is none', G.quarterTurns('sideways'), 0)
	check('None is none', G.quarterTurns(None), 0)
	check('negative wraps', G.quarterTurns(-90), 3)

	print('\n=== markers go through the same transform as the pixels ===')
	# End to end through the panel: a stub feed on a cropped camera, an
	# orientation set, and the cross must come out where orientPoint says.
	panel.markersBox.setChecked(True)
	prefix = panel.currentPrefix()
	# A cross at sensor pixel (400, 350) on a camera cropped to (281, 295) is at
	# (119, 55) in the frame, which readMarkers already does.
	panel.markerFeeds[prefix] = makeFeed(
		[{'x': StubPV(400.0), 'y': StubPV(350.0)}], roi=(281, 295))
	# refresh() needs a real camera, so drive the panel's own path with the
	# image step done here instead.
	frameWidth, frameHeight = 1292, 964
	for label, mirror, rotation in G.ORIENTATIONS:
		index = panel.orientCombo.findText(label)
		panel.orientCombo.setCurrentIndex(index)
		mk = panel.markerFeeds[prefix].readMarkers()
		moved = [G.orientPoint(x, y, frameWidth, frameHeight, mirror, rotation)
			+ (n,) for x, y, n in mk]
		panel.drawMarkers(moved)
		item = panel.markerItems[0]
		got = (item.getData()[0][0], item.getData()[1][0])
		expect = G.orientPoint(119.0, 55.0, frameWidth, frameHeight, mirror, rotation)
		check('{:<16} cross 1 at {}'.format(label, expect), got, expect)

	# And the corner cases of that: the identity leaves it alone, and a mirror
	# of a point near the left edge lands near the right edge, inside the frame.
	check('identity leaves the cross where it was',
		G.orientPoint(119.0, 55.0, frameWidth, frameHeight, False, 0), (119.0, 55.0))
	check('mirror stays inside the frame',
		G.orientPoint(0, 0, frameWidth, frameHeight, True, 0), (frameWidth - 1, 0))
	check('180 sends the first pixel to the last',
		G.orientPoint(0, 0, frameWidth, frameHeight, False, 180),
		(frameWidth - 1, frameHeight - 1))

	print('\n=== changing orientation clears the old crosses ===')
	panel.orientCombo.setCurrentIndex(0)
	panel.drawMarkers([(10.0, 20.0, 1)])
	panel.orientCombo.setCurrentIndex(3)
	check('cleared on the change', drawn(panel), {})
	check('and the levels are redone for the new shape', panel.needLevels, True)

	print('\n=== the wheel zoom is a third of pyqtgraph\'s ===')
	# Measured by sending real wheel events through a ViewBox and comparing the
	# view range before and after, not by reading the constant back, because the
	# thing that can break here is pyqtgraph renaming the state key - which the
	# constant would not notice and slowWheelZoom deliberately survives.
	import pyqtgraph as pg
	from pyqtgraph.Point import Point

	def zoomPerNotch(viewBox, notches=1, axis=0):
		# The factor the visible range changes by after `notches` wheel notches.
		# ViewBox.wheelEvent only uses delta() and pos() of the event, so a stub
		# is enough and no real input device is needed.
		#
		# axis matters: the histogram beside the image has its x axis mouse
		# disabled, so measuring x there reads 1.0 whatever the zoom rate is and
		# a check against it would pass for the wrong reason.
		class WheelEv:
			def __init__(self, delta):
				self._delta = delta

			def delta(self):
				return self._delta

			def pos(self):
				return Point(0.0, 0.0)

			def accept(self):
				pass

		viewBox.setRange(xRange=(0, 100), yRange=(0, 100), padding=0)
		before = viewBox.viewRange()[axis]
		for _ in range(notches):
			# 120 is one notch of a standard mouse wheel.
			viewBox.wheelEvent(WheelEv(120))
		after = viewBox.viewRange()[axis]
		return (after[1] - after[0]) / (before[1] - before[0])

	plain = pg.ViewBox()
	default = zoomPerNotch(plain)
	check('pyqtgraph default zooms about 35% per notch',
		abs(1.0 / default - 1.346) < 0.01, True)

	slowed = pg.ViewBox()
	check('slowWheelZoom reports success', G.slowWheelZoom(slowed), True)
	oneSlow = zoomPerNotch(slowed)
	check('one notch is now about 10%', abs(1.0 / oneSlow - 1.104) < 0.01, True)

	# The real claim: a third of the zoom per notch. pyqtgraph zooms by
	# 1.02 ** (delta * factor), so dividing the factor by three means three
	# notches cover exactly what one covered before.
	three = zoomPerNotch(slowed, notches=3)
	check('three notches equal one old notch', abs(three - default) < 1e-9, True)
	check('divisor is three', G.WHEEL_ZOOM_DIVISOR, 3.0)

	print('\n=== the camera image gets the gentler zoom ===')
	fresh = G.CameraPanel()
	check('the image zooms on both axes',
		fresh.imageView.getView().state['mouseEnabled'], [True, True])
	check('the image view is slowed on x',
		abs(1.0 / zoomPerNotch(fresh.imageView.getView(), axis=0) - 1.104) < 0.01, True)
	check('and equally on y',
		abs(1.0 / zoomPerNotch(fresh.imageView.getView(), axis=1) - 1.104) < 0.01, True)
	# The histogram's wheel zooms the level axis, which was not the complaint,
	# so it is deliberately left at pyqtgraph's rate. Measured on y, because its
	# x axis has the mouse disabled.
	hist = fresh.imageView.getHistogramWidget().item.vb
	check('the histogram y axis is the one it zooms',
		hist.state['mouseEnabled'], [False, True])
	check('the histogram is left at the default rate',
		abs(1.0 / zoomPerNotch(hist, axis=1) - 1.346) < 0.01, True)

	print('\n=== the zoom rate survives frames and camera changes ===')
	# It is set once in __init__, so anything that reaches into the view later
	# must not undo it. setImage is the one that runs every tick.
	view = fresh.imageView.getView()
	fresh.imageView.setImage(np.arange(400).reshape(20, 20).astype(float))
	fresh.onResetView()
	fresh.cameraCombo.setCurrentIndex(1)
	fresh.orientCombo.setCurrentIndex(2)
	check('still a third after setImage, reset, camera and orientation',
		abs(1.0 / zoomPerNotch(view) - 1.104) < 0.01, True)

	print('\n=== a pyqtgraph that renames the key is survivable ===')
	# slowWheelZoom reaches into another library's internals, so it has to fail
	# soft: the zoom goes back to the old rate, which is an annoyance and not a
	# broken window.
	class NoKey:
		state = {}
	check('a missing key is reported, not raised', G.slowWheelZoom(NoKey()), False)

	class NoState:
		pass
	check('no state at all is survivable', G.slowWheelZoom(NoState()), False)
	check('a zero divisor cannot raise',
		G.slowWheelZoom(pg.ViewBox(), divisor=0), False)

	print('\n=== orientation is display only ===')
	# The same guarantee as the markers themselves. Turning the view must not
	# write anything, and must not be reachable from the scan path.
	section = source[source.index('def orientImage'):source.index('def makePlaceholder')]
	check('no put in the transform', '.put(' not in section, True)
	check('no caput in the transform', 'caput' not in section, True)
	check('the scan builder never reads the orientation',
		'orientCombo' not in source[source.index('def buildScanList'):], True)

	print('\nFAILURES: {}'.format(bad))
	return bad


if __name__ == '__main__':
	sys.exit(1 if main() else 0)
