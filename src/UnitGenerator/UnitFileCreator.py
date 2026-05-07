# ---------------------------------------------------------------------------
# Field name declarations for each systemd unit file section.
# These are the only valid field names accepted by each section.
# ---------------------------------------------------------------------------

_UNIT_SECTION_FIELDS = """Description
Documentation
Wants
Requires
Requisite
BindsTo
PartOf
Upholds
Conflicts
Before
After
OnFailure
OnSuccess
PropagatesReloadTo
ReloadPropagatedFrom
PropagatesStopTo
StopPropagatedFrom
JoinsNamespaceOf
RequiresMountsFor
OnSuccessJobMode
OnFailureJobMode
IgnoreOnIsolate
StopWhenUnneeded
RefuseManualStart
RefuseManualStop
AllowIsolate
DefaultDependencies
CollectMode
FailureAction
SuccessAction
FailureActionExitStatus
SuccessActionExitStatus
JobTimeoutSec
JobRunningTimeoutSec
JobTimeoutAction
JobTimeoutRebootArgument
StartLimitIntervalSec
StartLimitBurst
StartLimitAction
SuccessAction
RebootArgument
SourcePath
ConditionArchitecture
ConditionFirmware
ConditionVirtualization
ConditionHost
ConditionKernelCommandLine
ConditionKernelVersion
ConditionCredential
ConditionEnvironment
ConditionSecurity
ConditionCapability
ConditionACPower
ConditionNeedsUpdate
ConditionFirstBoot
ConditionPathExists
ConditionPathExistsGlob
ConditionPathIsDirectory
ConditionPathIsSymbolicLink
ConditionPathIsMountPoint
ConditionPathIsReadWrite
ConditionPathIsEncrypted
ConditionDirectoryNotEmpty
ConditionFileNotEmpty
ConditionFileIsExecutable
ConditionUser
ConditionGroup
ConditionControlGroupController
ConditionMemory
ConditionCPUs
ConditionCPUFeature
ConditionOSRelease
ConditionMemoryPressure
ConditionCPUPressure
ConditionIOPressure
AssertArchitecture
AssertVirtualization
AssertHost
AssertKernelCommandLine
AssertKernelVersion
AssertCredential
AssertEnvironment
AssertSecurity
AssertCapability
AssertACPower
AssertNeedsUpdate
AssertFirstBoot
AssertPathExists
AssertPathExistsGlob
AssertPathIsDirectory
AssertPathIsSymbolicLink
AssertPathIsMountPoint
AssertPathIsReadWrite
AssertPathIsEncrypted
AssertDirectoryNotEmpty
AssertFileNotEmpty
AssertFileIsExecutable
AssertUser
AssertGroup
AssertControlGroupController
AssertMemory
AssertCPUs
AssertCPUFeature
AssertOSRelease
AssertMemoryPressure
AssertCPUPressure
AssertIOPressure
Alias
RequiredBy
Also
DefaultInstance
AssertPathExists"""

_INSTALL_SECTION_FIELDS = """Alias
WantedBy
RequiredBy
Also
DefaultInstance"""

_SERVICE_SECTION_FIELDS = """Type
ExitType
RemainAfterExit
GuessMainPID
PIDFile
BusName
ExecStart
ExecStartPre
ExecStartPost
ExecCondition
ExecReload
ExecStop
ExecStopPost
RestartSec
TimeoutStartSec
TimeoutStopSec
TimeoutAbortSec
TimeoutSec
TimeoutStartFailureMode
TimeoutStopFailureMode
RuntimeMaxSec
RuntimeRandomizedExtraSec
WatchdogSec
Restart
SuccessExitStatus
RestartPreventExitStatus
RestartForceExitStatus
RootDirectoryStartOnly
NonBlocking
NotifyAccess
Sockets
FileDescriptorStoreMax
USBFunctionDescriptors
USBFunctionStrings
OOMPolicy"""


# ---------------------------------------------------------------------------
# Unit_File — constructs and writes one systemd unit file
# ---------------------------------------------------------------------------

class Unit_File:

    def __init__(self):
        self.unit_file_dict  = self._create_unit_file_dict()
        self.exists_unit     = False
        self.exists_install  = False
        self.exists_service  = False

    def _create_unit_file_dict(self) -> dict:

        unit_dict    = {}
        install_dict = {}
        service_dict = {}

        for i in _UNIT_SECTION_FIELDS.split('\n'):
            unit_dict[str(i)] = ''
        for i in _INSTALL_SECTION_FIELDS.split('\n'):
            install_dict[str(i)] = ''
        for i in _SERVICE_SECTION_FIELDS.split('\n'):
            service_dict[str(i)] = ''

        return {
            'UNIT'    : unit_dict,
            'INSTALL' : install_dict,
            'SERVICE' : service_dict,
        }

    def edit_field(self, section_name: str, field_name: str, value: str):
        if section_name == 'UNIT':
            self.exists_unit    = True
        elif section_name == 'INSTALL':
            self.exists_install = True
        elif section_name == 'SERVICE':
            self.exists_service = True
        self.unit_file_dict[section_name][field_name] = value

    def dump_unit_file(self, unit_file_name: str, path: str):

        f = open(path + unit_file_name, 'w')

        if self.exists_unit:
            f.write('[Unit]\n')
            for i in self.unit_file_dict['UNIT']:
                if self.unit_file_dict['UNIT'][i] != '':
                    f.write(i + '=' + self.unit_file_dict['UNIT'][i] + '\n')
            f.write('\n')

        if self.exists_service:
            f.write('[Service]\n')
            for i in self.unit_file_dict['SERVICE']:
                if self.unit_file_dict['SERVICE'][i] != '':
                    f.write(i + '=' + self.unit_file_dict['SERVICE'][i] + '\n')
            f.write('\n')

        if self.exists_install:
            f.write('[Install]\n')
            for i in self.unit_file_dict['INSTALL']:
                if self.unit_file_dict['INSTALL'][i] != '':
                    f.write(i + '=' + self.unit_file_dict['INSTALL'][i] + '\n')
            f.write('\n')

        f.close()


# ---------------------------------------------------------------------------
# generate_all — orchestrates unit file creation for all instances.
# active_rule_set is passed in by the caller (UnitGenerator.py) to avoid a circular
# import between UnitFileCreator and Builders.
# ---------------------------------------------------------------------------

def generate_all(instances: list, active_rule_set, output_path: str = './') -> None:
    """
    Iterates over all GeneratedCommand instances and produces one systemd
    unit file per instance.

    active_rule_set is received as a parameter rather than imported, breaking
    the circular dependency that would arise from importing Builders here
    while Builders imports Unit_File from this file.

    Parameters
    ----------
    instances     : list of GeneratedCommand objects produced by CommandGenerator.
    active_rule_set : the RuleSetDescriptor instance selected in Rules.py.
    output_path   : directory where unit files are written. Defaults to the
                    current working directory. A trailing '/' is ensured.
    """
    if not output_path.endswith('/'):
        output_path += '/'

    for instance in instances:
        active_rule_set.unit_file_builder(instance, instances, output_path)
