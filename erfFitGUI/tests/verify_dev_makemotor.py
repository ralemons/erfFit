# Checks erfFit_dev.makeMotor builds a device and carries the scan parameters.
# Randy Lemons

# makeMotor used to pass five arguments positionally, which the motor(Newport)
# subclass in erfFit.py accepted and a plain pcdsdevices class does not: only
# prefix is positional and name is keyword only, both from ophyd's Device. That
# raised TypeError before connecting, so _dev could not run a scan at all.
#
# pcdsdevices is not installed on S3DF, so the real classes cannot be imported.
# Newport derives from ophyd's EpicsMotor and a TwinCAT axis from ophyd's
# PVPositioner, both of which ARE here, and the argument passing being checked
# is entirely ophyd's Device.__init__, which is common to the two, so the
# substitution does not weaken the check. Same technique as test_scanmotors.py.
#
# erfFit_dev resolves the class from the PV string the same way the GUI fork
# does, so the PVs below have to name a motor type or motorClass refuses them
# before makeMotor gets a chance.
#
# Nothing connects and nothing moves. The PVs below do not exist, and
# constructing a device is not a move.

import os
import sys
import types

DEVDIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, DEVDIR)

from ophyd import Component as Cpt
from ophyd import EpicsMotor, EpicsSignal, EpicsSignalRO, PVPositioner


class StubMotor(EpicsMotor):
	pass


class StubTwinCATBase(PVPositioner):
	pass


class StubTwinCAT(StubTwinCATBase):
	setpoint = Cpt(EpicsSignal, '.VAL')
	readback = Cpt(EpicsSignal, '.RBV')
	done = Cpt(EpicsSignalRO, '.DMOV')
	done_value = 1


fakePcds = types.ModuleType('pcdsdevices')

fakeMotor = types.ModuleType('pcdsdevices.epics_motor')
fakeMotor.Newport = StubMotor
fakeMotor.EpicsMotorInterface = EpicsMotor

fakeTwinCAT = types.ModuleType('pcdsdevices.twincat_motor')
fakeTwinCAT.TwinCATMotorInterface = StubTwinCATBase
fakeTwinCAT.TwinCATMREAxis = StubTwinCAT

fakePcds.epics_motor = fakeMotor
fakePcds.twincat_motor = fakeTwinCAT
sys.modules['pcdsdevices'] = fakePcds
sys.modules['pcdsdevices.epics_motor'] = fakeMotor
sys.modules['pcdsdevices.twincat_motor'] = fakeTwinCAT

fakeMpl = types.ModuleType('matplotlib')
fakePlt = types.ModuleType('matplotlib.pyplot')
for attr in ('figure', 'clf', 'plot', 'xlabel', 'ylabel', 'title', 'show'):
	setattr(fakePlt, attr, lambda *a, **k: None)
fakeMpl.pyplot = fakePlt
sys.modules.setdefault('matplotlib', fakeMpl)
sys.modules.setdefault('matplotlib.pyplot', fakePlt)

import numpy as np

import erfFit_dev

failures = []


def check(label, cond):
	if cond:
		print('  ok   ' + label)
	else:
		print('  FAIL ' + label)
		failures.append(label)


print('makeMotor builds a device')

m = erfFit_dev.makeMotor('UED:USR:MMN:04:03', 'X Motor',
		np.array([39.0, 38.5, 38.0]), True,
		['UED:USR:MMN:04:03', 'X Motor', '39.0:3:38.0', True])

check('returns the class the resolver gave', isinstance(m, StubMotor))
check('prefix is the pv', m.prefix == 'UED:USR:MMN:04:03')
check('name is the motor name, not the pv', m.name == 'X Motor')
check('positions carried through', list(m.positions) == [39.0, 38.5, 38.0])
check('indep carried through', m.indep is True)
check('userInput carried through', m.userInput[2] == '39.0:3:38.0')

print('defaults')

bare = erfFit_dev.makeMotor('UED:USR:MMN:04:02')
check('name defaults to undefined', bare.name == 'undefined')
check('positions defaults to a list, not None', bare.positions == [])
check('indep defaults to None', bare.indep is None)
check('userInput defaults to None', bare.userInput is None)

print('the downstream call sites that broke')

# runMotor does np.zeros_like(motor.positions) and len(motor.positions).
check('np.zeros_like on positions', len(np.zeros_like(m.positions)) == 3)
check('len on positions', len(m.positions) == 3)

# runConfig compares .indep to True/False, and saveConfig reads .userInput.
check('indep compares to True', (m.indep == True) is True)
check('a held motor holds a float position',
		erfFit_dev.makeMotor('UED:USR:MMN:04:01', 'Y', 9.5, False).positions == 9.5)

print('scan.addMotor, the path a config load takes')

s = erfFit_dev.scan()
s.addMotor(['UED:USR:MMN:04:03', 'X Motor', '39.0:3:38.0', True])
s.addMotor(['UED:USR:MMN:04:01', 'Y Motor', '9.5', False])

check('two motors added', len(s.motors) == 2)
check('scanning motor expanded by linspace',
		list(np.round(s.motors[0].positions, 3)) == [39.0, 38.5, 38.0])
check('scanning motor indep True', s.motors[0].indep is True)
check('held motor is a float', s.motors[1].positions == 9.5)
check('held motor indep False', s.motors[1].indep is False)

# saveConfig writes userInput back out, so a load/save round trip needs it.
check('userInput survives for saveConfig',
		s.motors[0].userInput == ['UED:USR:MMN:04:03', 'X Motor', '39.0:3:38.0', True])

print('motorClass resolves the two supported types and refuses the rest')

# The resolver is string matching on the PV now, so there is nothing to patch:
# an unsupported PV is refused by erfFit_dev.motorClass itself, before any
# device is constructed. Checked through makeMotor, which is the only caller.
check('an MMN is a Newport', erfFit_dev.motorClass('UED:USR:MMN:04:03') is StubMotor)
check('an MMS is a TwinCAT axis', erfFit_dev.motorClass('UED:UM1:MMS:02') is StubTwinCAT)

for pv in ('NOT:A:MOTOR', 'UED:USR:MMB:01:01', 'UED:USR:HEX:01:01', ''):
	try:
		erfFit_dev.makeMotor(pv)
		check('makeMotor refuses {!r}'.format(pv), False)
	except TypeError:
		check('makeMotor refuses {!r}'.format(pv), True)

# A PV naming both types is a PCDS naming accident, and guessing which one was
# meant would drive the motor through the wrong record layout.
try:
	erfFit_dev.motorClass('UED:USR:MMS:MMN:01')
	check('refuses a PV naming both types', False)
except TypeError:
	check('refuses a PV naming both types', True)

# This fork is the one that got the tabs-and-spaces mix and the pcdsdecives
# typo when the two type table landed, neither of which shows up until the
# module is imported. Importing it at all is therefore part of the check.
check('erfFit_dev imports cleanly', erfFit_dev.motorClass is not None)

print('')
if failures:
	print('{} FAILED'.format(len(failures)))
	for f in failures:
		print('  ' + f)
	sys.exit(1)

print('all checks passed')
