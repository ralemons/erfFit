# Where the erfFit GUI looks for configs and scan data
# Randy Lemons

# Imports os only.
#
# Nothing in the GUI hard codes a directory. Every starting location comes from
# here, and every one can be overridden with an environment variable, so the
# same files work in this folder, on the UED consoles, or anywhere else without
# editing the source.
#
#	ERFFIT_CONFIG_DIR	where Load config and Save config start
#	ERFFIT_DATA_DIR		where Plot saved scan starts, and where a finished
#				scan would write its csv
#
# When a variable is not set, the fallbacks below are tried in order and the
# first directory that exists wins. The directory holding these scripts is
# always the last resort, so the GUI still works from a bare copy of the folder.

import os

CONFIG_DIR_VAR = 'ERFFIT_CONFIG_DIR'
DATA_DIR_VAR = 'ERFFIT_DATA_DIR'
MODULE_DIR_VAR = 'ERFFIT_MODULE_DIR'

# Candidates tried when the environment variable is not set. Edit these once,
# per site, rather than hunting through the GUI code.
#
# uedopr moved from /cds/home/opr/uedopr to /sdf/home/u/uedopr. Everything from
# the uedopr directory down is unchanged, so only the base differs. The old
# location is kept at the end of each list, harmlessly, in case these files are
# ever run somewhere that still has it mounted.
UED_HOME = '/sdf/home/u/uedopr'
UED_HOME_OLD = '/cds/home/opr/uedopr'

CONFIG_DIR_FALLBACKS = [
	'~/erfFitGUI',
	'~/knifeEdgeConfigs',
	UED_HOME + '/rlemons/erfFitGUI',
	UED_HOME_OLD + '/rlemons/erfFitGUI',
]

DATA_DIR_FALLBACKS = [
	'~/opsKnifeScans',
	UED_HOME + '/rlemons/opsKnifeScans',
	UED_HOME_OLD + '/rlemons/opsKnifeScans',
	'./opsKnifeScans',
	'./exampleData',
]


def scriptDir():
	return os.path.dirname(os.path.abspath(__file__))


def resolveDirWithSource(variable, fallbacks):
	# Environment variable first, then the fallbacks, then the script folder.
	# Always returns a directory that exists, so a file dialog never opens on a
	# path that is not there. Returns (path, source) where source says where the
	# answer came from, so the startup message cannot claim a variable was used
	# when it was actually rejected.
	fromEnv = os.environ.get(variable)
	if fromEnv:
		expanded = os.path.realpath(os.path.expanduser(os.path.expandvars(fromEnv)))
		if os.path.isdir(expanded):
			return expanded, 'from ' + variable
		# A set but missing variable is worth saying out loud, because the
		# alternative is silently opening somewhere unexpected.
		print('{} is set to {!r}, which is not a directory. Falling back.'.format(
			variable, fromEnv))

	for candidate in fallbacks:
		expanded = os.path.expanduser(os.path.expandvars(candidate))
		if not os.path.isabs(expanded):
			expanded = os.path.join(scriptDir(), expanded)
		expanded = os.path.realpath(expanded)
		if os.path.isdir(expanded):
			return expanded, 'default'

	return scriptDir(), 'script folder'


def resolveDir(variable, fallbacks):
	return resolveDirWithSource(variable, fallbacks)[0]


def moduleSearchDirs():
	# Extra directories to look in for erfFit.py, which the motor controls
	# import. It is usually beside the GUI, which python already covers, but it
	# is often one level up with the GUI in its own folder, so that is added
	# too. ERFFIT_MODULE_DIR overrides for anywhere else.
	dirs = []

	fromEnv = os.environ.get(MODULE_DIR_VAR)
	if fromEnv:
		expanded = os.path.realpath(os.path.expanduser(os.path.expandvars(fromEnv)))
		if os.path.isdir(expanded):
			dirs.append(expanded)

	here = scriptDir()
	dirs.append(here)
	dirs.append(os.path.realpath(os.path.join(here, os.pardir)))

	return [d for d in dirs if os.path.isdir(d)]


def configDir():
	return resolveDir(CONFIG_DIR_VAR, CONFIG_DIR_FALLBACKS)


def dataDir():
	return resolveDir(DATA_DIR_VAR, DATA_DIR_FALLBACKS)


def describe():
	# Printed by the GUI at startup, so there is never any doubt about which
	# directories it is using.
	lines = []
	for name, variable, fallbacks in (
			('configs', CONFIG_DIR_VAR, CONFIG_DIR_FALLBACKS),
			('scan data', DATA_DIR_VAR, DATA_DIR_FALLBACKS)):
		value, source = resolveDirWithSource(variable, fallbacks)
		lines.append('  {:<10} {}  ({})'.format(name, value, source))
	return '\n'.join(lines)


if __name__ == '__main__':
	print('erfFit GUI directories:')
	print(describe())
