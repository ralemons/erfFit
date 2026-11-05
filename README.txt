erfFit
Randy Lemons

A lightweight set of python tools for knife edge measurements at UED. A motor
carries a knife edge across the pump beam at the interaction point while a data
PV is read at each position; the resulting S curve is fit to a complementary
error function, and the width of that erf gives the spot size at the IP.

The tools are deliberately small: pyepics for the data PVs, pcdsdevices for the
motors, numpy and scipy for the fit. There is no framework, no database and no
state kept between runs. A measurement is a text config in, one csv per scan
out, and a spot size printed or plotted.


WHAT A KNIFE EDGE SCAN IS, HERE

One motor scans and the others are held. The scanning motor steps through a
range of positions, the data PV is read numAvgs times at each position and
averaged, and the pair (position, average) is a data point. The knife
progressively blocks the beam, so the measured values trace a step whose
sharpness is the beam size.

The fit function is

	y = y0 + (A/2) * erfc( (sqrt(2)/w) * (x - x0) )

with four free parameters: y0 the background offset, A the amplitude, x0 the
edge centre, and w the width. For an intensity profile exp(-2 r^2 / w^2), w is
the 1/e^2 radius, so the numbers usually quoted follow from it:

	2w		spot size, 2 * |w|
	FWHM		sqrt(2 ln2) * |w|

Motor positions are in mm, so every width derived from w is in mm. The data PV
is in whatever units it reports and the amplitude is arbitrary; nothing in the
fit depends on its scale.

A spot size at the IP needs both axes, so a typical config is two scans: one
scanning the horizontal motor with the vertical held, then the reverse.


LAYOUT OF THIS REPOSITORY

The top level holds the base module, and each sub-folder is a version of that
module built to solve a different problem. 

	erfFit.py		the base module. This is the 'stable' version

	erfFit_dev.py	working copy: changes land here first and are
				folded back into erfFit.py once they have been
				run at UED. 

	erfFitGUI/		the GUI version. See below.
	erfFitEPICS/		the motor agnostic version. See below.

Each sub-folder carries its own erfFit.py. These are NOT copies of the base
file, they are forks of it, and they have diverged


THE BASE MODULE

erfFit.py is imported, not run. It has no __main__ and no command line; it is
used from a python prompt on a UED console.

	import erfFit

	erfFit.runConfig('gued_knifeEdge.txt')	run a saved config
	erfFit.makeConfig()			build one interactively
	erfFit.saveConfig(cfg, 'name.txt')	write it out
	erfFit.plotFile('scan.csv')		re-fit and plot a saved scan
	erfFit.motorInput()			one scan, all parameters prompted
	erfFit.inputData()			type x,y pairs in by hand

runConfig is the normal entry point. It takes either a path to a config or an
already built list of scan objects, sets the held motors, runs the scanning
motor, saves a csv and plots the fit, then returns the motors to where they
started.


THE CONFIG FILE FORMAT

A flat text file, read top to bottom, counts first. Every version in this
repository reads and writes the same format, which is what lets a config be
moved between them.

	2					number of scans
	2,1,10					numMotors, numDataPVs, numAvgs
	UED:USR:MMN:04:03,X Motor,39.0:30:38.0,True
	UED:USR:MMN:04:02,Y Motor,9.5,False
	SCOP:AS01:SC01:AI_CH1_AREA
	2,1,10					scan 2 starts here
	UED:USR:MMN:04:03,X Motor,35.0,False
	UED:USR:MMN:04:02,Y Motor,11:30:10,True
	SCOP:AS01:SC01:AI_CH1_AREA

A motor line is PV, name, positions, indep. Note that low and high are given in
the order they will be driven, so 39.0:30:38.0 scans downward. The data is
sorted by position before fitting, so the direction does not affect the fit.


OUTPUT

One csv per scan, two columns, position and averaged value, sorted by position
and no header:

	13.5,-1.431040016305633e-10
	13.569,-1.3936000187648463e-10

That is the whole data product. The fit is not saved by the base module - it is
recomputed from the csv whenever it is wanted, by plotFile or by the GUI, so
there is only ever one copy of the data and the fit cannot go stale against it.

The GUI version additionally writes a <base>_summary.csv per run, one row per
scan, with the motor, the data PV, the fit parameters, 2w, FWHM and R squared,
rewritten after each scan so an aborted run still has a summary of what
finished.


erfFitGUI/

A PyQt5 GUI wrapped around the module: a live camera view, the scan parameters
of a loaded config, editing of those parameters, motor jog controls, and a
results window with the erf fits. Built so that a knife edge measurement can be
set up and run without typing anything at a python prompt.

	./erfFit_GUI.py				on the UED consoles
	./erfFit_GUI.py test.txt		load a config at startup

The GUI itself needs PyQt5, pyqtgraph, numpy, scipy and pyepics, and does NOT
need pcdsdevices - it only imports erfFit when the Connect motors button is
pressed, so the window comes up and a config can be edited anywhere. Running a
scan needs pcdsdevices and a route to UED.

Its erfFit.py has diverged furthest from the base. The substantive changes:

	positions, indep and userInput moved from the motor onto the scan, so
	one device per PV can be shared by every scan that uses it, rather than
	needing a separate device object per scan at 19 channel access channels
	each.

	getMotor(pv, name, motorDict) returns the existing device when the PV is
	already in motorDict, which is how the GUI's jog rows and a running scan
	drive the same objects.

	Move completion decided from the motor record's .DMOV and the readback
	rather than from the MoveStatus that mv() returns, with a settle period
	and a fallback for records with no .DMOV.

	runConfig takes shouldAbort and report callables, so a scan can be
	stopped between points and can report progress to a GUI, and plotRun and
	saveRun flags so the plotting can be done by the GUI instead of
	matplotlib.

	Save names derived from the base name and the scanning motor, with the
	save directory and name clashes checked before the first motor moves.

	Position range checks against the motor limits for every scan before
	anything moves.


erfFitEPICS/

The motor agnostic version. Some motors at UED have working EPICS PVs but no
usable pcdsdevices class, so this version drops pcdsdevices entirely and talks
to the motor record directly.

	motor is a plain class, not a Newport subclass.
	position() is caget on <pv>.RBV.
	move() is caput to <pv>.VAL, then poll .RBV until it is within tolerance
	of the target for a settle period, with a timeout.

Use this when a motor will not come up as a pcdsdevices device. Use the base
module or the GUI otherwise - this version gives up the limit checking and the
proper move status that pcdsdevices provides.
