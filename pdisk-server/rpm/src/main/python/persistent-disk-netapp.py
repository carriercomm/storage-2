#!/usr/bin/env python
"""
 Created as part of the StratusLab project (http://stratuslab.eu),
 co-funded by the European Commission under the Grant Agreement
 INSFO-RI-261552.

 Copyright (c) 2011, Centre National de la Recherche Scientifique (CNRS)

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

 http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

"""
Script used by StratusLab pdisk to manage iSCSI LUNs
"""

__version__ = "1.1.1-1"
__author__  = "Michel Jouvin <jouvin@lal.in2p3.fr>"

import sys
import os
import os.path
import re
from subprocess import *
import StringIO
from optparse import OptionParser
import logging
import logging.handlers
import syslog
import ConfigParser

# Initializations
verbosity = 0
logger = None
action_default = ''
status = 0           # Assume success

# Supported iSCSI proxy variants
iscsi_supported_variants = [ 'lvm', 'netapp' ]

# Keys are supported actions, values are the number of arguments required for the each action
valid_actions = { 'check':1, 'create':2, 'delete':1, 'rebase':2, 'snapshot':3 }
valid_actions_str = ', '.join(valid_actions.keys())

config_file_default = '/opt/stratuslab/etc/persistent-disk-backend.conf'
config_main_section = 'main'
config_defaults = StringIO.StringIO("""
# Options commented out are configuration options available for which no 
# sensible default value can be defined.
[main]
# Define the list of iSCSI proxies that can be used.
# One section per proxy must also exists to define parameters specific to the proxy.
#iscsi_proxies=netapp.example.org
# Log file for persistent disk management
log_file=/var/log/stratuslab-persistent-disk.log
# User name to use to connect the filer (may also be defined in the filer section)
mgt_user_name=root
# SSH private key to use for 'mgt_user_name' authorisation
#mgt_user_private_key=/some/file.rsa

#[netapp.example.org]
# iSCSI back-end type (case insensitive)
#type=NetApp
# Initiator group the LUN must be mapped to
#initiator_group = linux_servers
# Name appended to the volume name to build the LUN path (a / will be appended)
#lun_namespace=stratuslab
# Volume name where LUNs will be created
#volume_name = /vol/iscsi
# Name prefix to use to build the volume snapshot used as a LUN clone snapshot parent
# (a _ will be appended)
#volume_snapshot_prefix=pdisk_clone

#[lvm.example.org]
# iSCSI back-end type (case insensitive)
#type=LVM
# LVM volume group to use for creating LUNs
#volume_name = /dev/iscsi.01

""")


############################################
# Class describing a NetApp iSCSI back-end #
############################################

class NetAppBackend:
  # Command to connect to NetApp filer
  cmd_prefix = [ 'ssh', '-x', '-i', '%%PRIVKEY%%','%%ISCSI_PROXY%%' ]
  
  # Table defining mapping of LUN actions to NetApp actions.
  # This is a 1 to n mapping (several NetApp commands may be needed for one LUN action).
  # map and unmap are necessary as separate actions as they are not necessary executed on
  # the same LUN as the other operations (eg. snapshot action).
  lun_backend_cmd_mapping = { 'check':['check'],
                            'create':['create','map'],
                            # Attemtp to delete volume snapshot associated with the LUN if it is no longer used (no more LUN clone exists)
                            'delete':['unmap','delete','snapdel'],
                            'map':['map'],
                            'rebase':None,
                            'snapshot':['snapshot','clone'],
                            'unmap':['unmap']
                            }
  
  # Definitions of NetApp commands used to implement actions.
  backend_cmds = {'check':[ 'lun', 'show', '%%NAME%%' ],
                  'clone':[ 'lun', 'clone', 'create', '%%SNAP_NAME%%', '-b', '%%NAME%%', '%%SNAP_PARENT%%'  ],
                  'create':[ 'lun', 'create', '-s', '%%SIZE%%g', '-t', '%%LUNOS%%', '%%NAME%%' ],
                  'delete':[ 'lun', 'destroy', '%%NAME%%' ],
                  'map':[ 'lun', 'map', '-f', '%%NAME%%', '%%INITIATORGRP%%' ],
                  'snapdel':[ 'snap', 'delete', '%%VOLUME_NAME%%', '%%SNAP_PARENT%%' ],
                  'snapshot':[ 'snap', 'create', '%%VOLUME_NAME%%', '%%SNAP_PARENT%%'  ],
                  'unmap':[ 'lun', 'unmap', '%%NAME%%', '%%INITIATORGRP%%' ]
                  }

  # Most commands are expected to return nothing when they succeeded. The following
  # dictionnary lists exceptions and provides a pattern matching output in case of
  # success.
  # Keys must match an existing key in backend_cmds
  success_msg_pattern = { 'check':'online',
                          # snapdel is expected to fail if there is still a LUN clone using it or if the snapshot doesnt exist
                          # (LUN never cloned or is a clone). These are not considered as an error.
                          'snapdel':[ 'deleting snapshot\.\.\.', 'Snapshot [\w\-]+ is busy because of LUN clone','No such snapshot' ],
                          'snapshot':['^creating snapshot','^Snapshot already exists.']
                        }
  # Would be great to have it configurable as NetApp needs to know the client OS
  lunOS = 'linux'
  
  def __init__(self,proxy,mgtUser,mgtPrivKey,volume,namespace,initiatorGroup,snapshotPrefix):
    self.proxyHost = proxy
    self.mgtUser = mgtUser
    self.mgtPrivKey = mgtPrivKey
    self.volumePath = volume
    self.volumeName = volume.split('/')[-1]
    self.namespace = "%s/%s" % (self.volumePath,namespace)
    self.initiatorGroup = initiatorGroup
    self.snapshotPrefix = snapshotPrefix

  # Generator function returning:
  #    - the command corresponding to the action as a list of tokens, with iSCSI proxy related
  #      variables parsed.
  #    - the expected message patterns in case of success if the command output is not empty. This is returned as
  #      a list of patterns (a simple string is converted to a list).
  # This function must be called from an iteration loop control statement
  def getCmd(self,lun_action):
    if lun_action in self.lun_backend_cmd_mapping:
      backend_actions = self.lun_backend_cmd_mapping[lun_action]
    else:
      abort("Internal error: LUN action '%s' unknown" % (lun_action))

    # If None, means that the action is not implemented
    if backend_actions == None:
      yield backend_actions,None
          
    # Intialize parsed_command and success_patters in case backend_actions is an empty list
    parsed_command = []
    success_patterns = None
    for action in backend_actions:
      if action in self.backend_cmds.keys():
        command = self.backend_cmds[action]
        parsed_command = self.parse(command)
      else:
        abort("Internal error: action '%s' unknown" % (action))
  
      if action in self.success_msg_pattern:
        success_patterns = self.success_msg_pattern[action]
        if isinstance(success_patterns,str):
          success_patterns = [ success_patterns ]
      else:
        success_patterns = None
        
      yield parsed_command,success_patterns
    
  # Add command prefix and parse all variables related to iSCSI proxy in the command (passed as a list of tokens).
  # Return parsed command as a list of token.
  def parse(self,command):    
    # Build command to execute
    action_cmd = []
    action_cmd.extend(self.cmd_prefix)
    action_cmd.extend(command)
    for i in range(len(action_cmd)):
      if action_cmd[i] == '%%INITIATORGRP%%':
        action_cmd[i] = self.initiatorGroup
      elif action_cmd[i] == '%%LUNOS%%':
        action_cmd[i] = self.lunOS
      elif action_cmd[i] == '%%PRIVKEY%%':
        action_cmd[i] = self.mgtPrivKey
      elif action_cmd[i] == '%%ISCSI_PROXY%%':
        action_cmd[i] = "%s@%s" % (self.mgtUser,self.proxyHost)
      elif action_cmd[i] == '%%SNAP_PARENT%%':
        action_cmd[i] = self.snapshotPrefix + '_%%UUID%%'
      elif action_cmd[i] == '%%NAME%%':
        action_cmd[i] = self.namespace + "/%%UUID%%"
      elif action_cmd[i] == '%%SNAP_NAME%%':
        action_cmd[i] = self.namespace + "/%%SNAP_UUID%%"
      elif action_cmd[i] == '%%VOLUME_NAME%%':
        action_cmd[i] = self.volumeName
    return action_cmd
    
  # Return iSCSI back-end type
  def getType(self):
    return 'NetApp'


#########################################
# Class describing a LVM iSCSI back-end #
#########################################

class LVMBackend:
  # Command prefix to use to connect through ssh
  ssh_cmd_prefix = [ 'ssh', '-x', '-i', '%%PRIVKEY%%','%%ISCSI_PROXY%%' ]
  # Command to connect to NetApp filer
  cmd_prefix = [ ]
  
  # Table defining mapping of LUN actions to NetApp actions.
  # This is a 1 to n mapping (several NetApp commands may be needed for one LUN action).
  # map and unmap are necessary as separate actions as they are not necessary executed on
  # the same LUN as the other operations (eg. snapshot action).
  lun_backend_cmd_mapping = { 'check':['check'],
                              'create':['create'],
                              'delete':['remove'],
                              # map is a required action for snapshot action but does nothing in LVM
                              'map':[],
                              'rebase':['rebase'],
                              'snapshot':['snapshot']
                              }
  
  # Definitions of LVM commands used to implement actions.
  backend_cmds = {'check':[ '/usr/bin/test', '-b', '%%LOGVOL_PATH%%' ],
                  'create':[ '/sbin/lvcreate', '-L', '%%SIZE%%G', '-n', '%%UUID%%', '%%VOLUME_NAME%%' ],
                  'remove':[ '/sbin/lvremove', '-f', '%%LOGVOL_PATH%%' ],
                  'rebase':[ '/bin/dd', 'if=%%LOGVOL_SRC_PATH%%', 'of=%%LOGVOL_PATH%%'],
                  # lvchange doesn't work with clone. Normally unneeded as lvremove -f (remove) does the same
                  'setinactive':[ '/sbin/lvchange', '-a', 'n', '%%LOGVOL_PATH%%' ],
                  'snapshot':[ '/sbin/lvcreate', '--snapshot', '-p', 'rw', '--size', '%%SIZE%%G', '-n', '%%SNAP_UUID%%', '%%LOGVOL_PATH%%'  ],
                  }

  # Most commands are expected to return nothing when they succeeded. The following
  # dictionnary lists exceptions and provides a pattern matching output in case of
  # success.
  # Keys must match an existing key in backend_cmds
  success_msg_pattern = {'create':'Logical volume "[\w\-]+" created',
                         'remove':'Logical volume "[\w\-]+" successfully removed',
                         'rebase':'\d+ bytes .* copied',
                         'setinactive':[ '^$', 'File descriptor .* leaked on lvchange invocation' ],
                         'snapshot':'Logical volume "[\w\-]+" created'
                        }
  
  def __init__(self,proxy,volume,mgtUser=None,mgtPrivKey=None):
    self.volumeName = volume
    self.proxyHost = proxy
    self.mgtUser = mgtUser
    self.mgtPrivKey = mgtPrivKey
    if self.mgtUser and self.mgtPrivKey:
      debug(1,'SSH will be used to connect to LVM backend')
      self.cmd_prefix = self.ssh_cmd_prefix

  # Generator function returning:
  #    - the command corresponding to the action as a list of tokens, with iSCSI proxy related
  #      variables parsed.
  #    - the expected message patterns in case of success if the command output is not empty. This is returned as
  #      a list of patterns (a simple string is converted to a list).
  # This function must be called from an iteration loop control statement
  def getCmd(self,lun_action):
    if lun_action in self.lun_backend_cmd_mapping:
      backend_actions = self.lun_backend_cmd_mapping[lun_action]
    else:
      abort("Internal error: LUN action '%s' unknown" % (lun_action))

    # If None, means that the action is not implemented
    if backend_actions == None:
      yield backend_actions,None
          
    # Intialize parsed_command and success_patters in case backend_actions is an empty list
    parsed_command = []
    success_patterns = None
    for action in backend_actions:
      if action in self.backend_cmds.keys():
        command = self.backend_cmds[action]
        parsed_command = self.parse(command)
      else:
        abort("Internal error: action '%s' unknown" % (action))
  
      if action in self.success_msg_pattern:
        success_patterns = self.success_msg_pattern[action]
        if isinstance(success_patterns,str):
          success_patterns = [ success_patterns ]
      else:
        success_patterns = None
        
      yield parsed_command,success_patterns
    
  # Add command prefix and parse all variables related to iSCSI proxy in the command (passed as a list of tokens).
  # Return parsed command as a list of token.
  def parse(self,command):    
    # Build command to execute
    action_cmd = []
    action_cmd.extend(self.cmd_prefix)
    action_cmd.extend(command)
    for i in range(len(action_cmd)):
      if action_cmd[i] == '%%ISCSI_PROXY%%':
        action_cmd[i] = "%s@%s" % (self.mgtUser,self.proxyHost)
      elif re.search('%%LOGVOL_PATH%%',action_cmd[i]):
        action_cmd[i] = re.sub('%%LOGVOL_PATH%%',self.volumeName+"/%%UUID%%",action_cmd[i])
      elif re.search('%%LOGVOL_SRC_PATH%%',action_cmd[i]):
        action_cmd[i] = re.sub('%%LOGVOL_SRC_PATH%%',self.volumeName+"/%%SNAP_UUID%%",action_cmd[i])
      elif action_cmd[i] == '%%PRIVKEY%%':
        action_cmd[i] = self.mgtPrivKey
      elif action_cmd[i] == '%%VOLUME_NAME%%':
        action_cmd[i] = self.volumeName
    return action_cmd
    
  # Return iSCSI back-end type
  def getType(self):
    return 'LVM'


#################################################################
# Class describing a LUN and implementing the supported actions #
#################################################################

class LUN:

  def __init__(self,uuid,size=None,proxy=None):
    self.uuid = uuid
    self.size = size
    self.proxy = proxy
    self.snapshotLUN = None
    
  def check(self):
    return self.__executeAction__('check')
    
  def create(self):
    return self.__executeAction__('create')
    
  def delete(self):
    return self.__executeAction__('delete')
    
  def map(self):
    return self.__executeAction__('map')
    
  def rebase(self,src_lun):
    self.snapshotLUN = src_lun
    return self.__executeAction__('rebase')
    
  def snapshot(self,snapshot_lun):
    self.snapshotLUN = snapshot_lun
    return self.__executeAction__('snapshot')
    
  def unmap(self):
    return self.__executeAction__('unmap')
    
    
  # Execute an action on a LUN.
  # An action may involve several actual commands : getCmd() method of proxy is a generator returning
  # the commands to execute one by one.
  # In case an error occurs during one command, try to continue...
  # Return the status of the last command executed
  def __executeAction__(self,action):
    status = 0         # Assume success
    for cmd_toks,successMsg in self.proxy.getCmd(action):
      # When returned command for action is None, it means that the action is not implemented
      if cmd_toks == None:
        abort("Action '%s' not implemented by SCSI back-end type '%s'" % (action,self.proxy.getType()))
      command = Command(action,self.parse(cmd_toks),successMsg)
      command.execute()
      status = command.checkStatus()
    return status

  # Parse all variables related to current LUN in the command (passed and returned as a list of tokens).  
  def parse(self,action_cmd):
    for i in range(len(action_cmd)):
      if re.search('%%SIZE%%',action_cmd[i]):
        action_cmd[i] = re.sub('%%SIZE%%',self.size,action_cmd[i])
      elif re.search('%%UUID%%',action_cmd[i]):
        action_cmd[i] = re.sub('%%UUID%%',self.uuid,action_cmd[i])
      elif re.search('%%SNAP_UUID%%',action_cmd[i]):
        action_cmd[i] = re.sub('%%SNAP_UUID%%',self.snapshotLUN.uuid,action_cmd[i])
    return action_cmd
    

#######################################################
# Class representing a command passed to the back-end #
#######################################################
    
class Command:
  cmd_output_start = '<<<<<<<<<<'
  cmd_output_end = '>>>>>>>>>>'
  
  def __init__(self,action,cmd,successMsgs=None):
    self.action = action
    self.action_cmd = cmd
    self.successMsgs = successMsgs
    self.proc = None

  def execute(self):
    status = 0
    # Execute command: NetApp command don't return an exit code. When a command is sucessful,
    # its output is empty.
    debug(1,"Executing command: '%s'" % (' '.join(self.action_cmd)))
    try:
      self.proc = Popen(self.action_cmd, shell=False, stdout=PIPE, stderr=STDOUT)
    except OSError, details:
      abort('Failed to execute %s action: %s' % (self.action,details))
      status = 1
    return status
  
  def checkStatus(self):
    try:
      retcode = self.proc.wait()
      output = self.proc.communicate()[0]
      if retcode != 0:
          abort('An error occured during %s action (error=%s). Command output:\n%s\n%s\n%s' % (self.action,retcode,self.cmd_output_start,output,self.cmd_output_end))
      else:
          # Need to check if the command is expected to return an output when successfull
          success = False
          if self.successMsgs:
            for successPattern in self.successMsgs:
              if re.search(successPattern,output):
                success = True
                break
          else:
            if len(output) == 0:
              success = True
          if success:
            debug(1,'%s action completed successfully.' % (self.action))
            if len(output) > 0:
              debug(2,'Command output:\n%s\n%s\n%s' % (self.cmd_output_start,output,self.cmd_output_end))
          else:
            retcode = -1
            debug(0,'An error occured during %s action. Command output:\n%s\n%s\n%s' % (self.action,self.cmd_output_start,output,self.cmd_output_end))
    except OSError, details:
      abort('Failed to execute %s action: %s' % (self.action,details))  
    return retcode


###############################
# Functions to handle logging #
###############################

def abort(msg):
    logger.error("Persistent disk operation failed:\n%s" % (msg))
    sys.exit(2)

def debug(level,msg):
  if level <= verbosity:
    if level == 0:
      logger.info(msg)
    else:
      logger.debug(msg)



#############
# Main code #
#############

# Configure loggers and handlers.
# Initially cnfigure only syslog and stderr handler.

logging_source = 'stratuslab-pdisk'
logger = logging.getLogger(logging_source)
logger.setLevel(logging.DEBUG)

#fmt=logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
fmt=logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
# Handler used to report to SVN must display only the message to allow proper XML formatting
svn_fmt=logging.Formatter("%(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
logger.addHandler(console_handler)


# Parse configuration and options

usage_text = """usage: %prog [options] action_parameters

Parameters:
    action=check:    LUN_UUID
    action=create:   LUN_UUID LUN_Size
    action=delete:   LUN_UUID
    action=rebase:   LUN_UUID New_LUN_UUID
    action=snapshot: LUN_UUID New_LUN_UUID Snapshot_Size
"""

parser = OptionParser(usage=usage_text)
parser.add_option('--config', dest='config_file', action='store', default=config_file_default, help='Name of the configuration file to use (D: %s)' % (config_file_default))
parser.add_option('--action', dest='action', action='store', default=action_default, help='Action to execute. Valid actions: %s'%(valid_actions_str))
parser.add_option('-v', '--debug', '--verbose', dest='verbosity', action='count', default=0, help='Increase verbosity level for debugging (multiple allowed)')
parser.add_option('--version', dest='version', action='store_true', default=False, help='Display various information about this script')
options, args = parser.parse_args()

if options.version:
  debug (0,"Version %s written by %s" % (__version__,__author__))
  debug (0,__doc__)
  sys.exit(0)

if options.verbosity:
  verbosity = options.verbosity

if options.action in valid_actions:
  if len(args) < valid_actions[options.action]:
    debug(0,"Insufficient argument provided (%d required)" % (valid_actions[options.action]))  
    parser.print_help()
    abort("")
else:
  if options.action:
    debug(0,"Invalid action requested (%s)\n" % (options.action))
  else:
    debug(0,"No action specified\n")
  parser.print_help()
  abort("")
    

# Read configuration file.
# The file must exists as there is no sensible default value for several options.

config = ConfigParser.ConfigParser()
config.readfp(config_defaults)
try:
  config.readfp(open(options.config_file))
except IOError, (errno,errmsg):
  if errno == 2:
    abort('Configuration file (%s) is missing.' % (options.config_file))
  else:
    abort('Error opening configuration file (%s): %s (errno=%s)' % (options.config_file,errmsg,errno))
  
logfile_handler = None
try:
  log_file = config.get(config_main_section,'log_file')
  if log_file:
    logfile_handler = logging.handlers.RotatingFileHandler(log_file,'a',100000,10)
    logfile_handler.setLevel(logging.DEBUG)
    logfile_handler.setFormatter(fmt)
    logger.addHandler(logfile_handler)
except ValueError:
  abort("Invalid value specified for 'log_file' (section %s)" % (config_main_section))

if logfile_handler == None or not log_file:
  # Use standard log destination in case a log file is not defined
  syslog_handler = logging.handlers.SysLogHandler('/dev/log')
  syslog_handler.setLevel(logging.WARNING)
  logger.addHandler(syslog_handler)


try:
  iscsi_proxies_list = config.get(config_main_section,'iscsi_proxies')
  iscsi_proxies = iscsi_proxies_list.split(',')
  iscsi_proxy_name = iscsi_proxies[0]
except ValueError:
  abort("Invalid value specified for 'iscsi_proxies' (section %s) (must be a comma-separated list)" % (config_main_section))

try:
  backend_variant=config.get(iscsi_proxy_name,'type')  
except:
  abort("Section '%s' or required attribute 'type' missing" % (iscsi_proxy_name))

# NetApp back-end configuration

if backend_variant.lower() == 'netapp':
  # Retrieve NetApp back-end mandatory attributes.
  # Mandatory attributes should be defined as keys of backend_attributes with an arbitrary value.
  # Key name must match the attribute name in the configuration file.
  backend_attributes = {'initiator_group':'',
                        'lun_namespace':'',
                        'volume_name':'',
                        'volume_snapshot_prefix':''
                        }
  try:
    for attribute in backend_attributes.keys():
      backend_attributes[attribute]=config.get(iscsi_proxy_name,attribute)
  except:
    abort("Section '%s' or required attribute '%s' missing" % (iscsi_proxy_name,attribute))
  
  try:
    backend_attributes['mgt_user_name']=config.get(iscsi_proxy_name,'mgt_user_name')  
  except:
    try:
      backend_attributes['mgt_user_name']=config.get(config_main_section,'mgt_user_name')  
    except:
      abort("User name to use for connecting to iSCSI proxy undefined")
    
  try:
    backend_attributes['mgt_user_private_key']=config.get(iscsi_proxy_name,'mgt_user_private_key')  
  except:
    try:
      backend_attributes['mgt_user_private_key']=config.get(config_main_section,'mgt_user_private_key')  
    except:
      abort("SSH private key to use for connecting to iSCSI proxy undefined")
  
  # Create iSCSI back-end object  
  iscsi_proxy = NetAppBackend(iscsi_proxy_name,
                              backend_attributes['mgt_user_name'],
                              backend_attributes['mgt_user_private_key'],
                              backend_attributes['volume_name'],
                              backend_attributes['lun_namespace'],
                              backend_attributes['initiator_group'],
                              backend_attributes['volume_snapshot_prefix']
                              )

# LVM back-end configuration
elif backend_variant.lower() == 'lvm':
  # Retrieve NetApp back-end mandatory attributes.
  # Mandatory attributes should be defined as keys of backend_attributes with an arbitrary value.
  # Key name must match the attribute name in the configuration file.
  backend_attributes = {'volume_name':'',
                        }
  try:
    for attribute in backend_attributes.keys():
      backend_attributes[attribute]=config.get(iscsi_proxy_name,attribute)
  except:
    abort("Section '%s' or required attribute '%s' missing" % (iscsi_proxy_name,attribute))

  # 'local' is a reserved name to designate the local machine: in this case, don't use ssh to
  # connect to backend
  if iscsi_proxy_name == 'local':
    backend_attributes['mgt_user_name'] = None
    backend_attributes['mgt_user_private_key'] = None
  else:  
    try:
      backend_attributes['mgt_user_name']=config.get(iscsi_proxy_name,'mgt_user_name')  
    except:
      try:
        backend_attributes['mgt_user_name']=config.get(config_main_section,'mgt_user_name')  
      except:
        abort("User name to use for connecting to iSCSI proxy undefined")
      
    try:
      backend_attributes['mgt_user_private_key']=config.get(iscsi_proxy_name,'mgt_user_private_key')  
    except:
      try:
        backend_attributes['mgt_user_private_key']=config.get(config_main_section,'mgt_user_private_key')  
      except:
        abort("SSH private key to use for connecting to iSCSI proxy undefined")

  # Create iSCSI back-end object  
  iscsi_proxy = LVMBackend(iscsi_proxy_name,
                           backend_attributes['volume_name'],
                           backend_attributes['mgt_user_name'],
                           backend_attributes['mgt_user_private_key'],
                          )
  
# Abort if iSCSI back-end variant specified is not supported
else:
   abort("Unsupported iSCSI back-end variant '%s' (supported variants: %s)" % (backend_variant,','.join(iscsi_supported_variants)))   


# Execute requested action

if options.action == 'check':
  debug(1,"Checking LUN existence...")
  lun = LUN(args[0],proxy=iscsi_proxy)
  status = lun.check()
elif options.action == 'create':
  debug(1,"Creating LUN...")
  lun = LUN(args[0],size=args[1],proxy=iscsi_proxy)
  status = lun.create()
elif options.action == 'delete':
  debug(1,"Deleting LUN...")
  lun = LUN(args[0],proxy=iscsi_proxy)
  status = lun.delete()
elif options.action == 'rebase':
  debug(1,"Rebasing LUN...")
  src_lun = LUN(args[0],proxy=iscsi_proxy)
  rebased_lun = LUN(args[1],proxy=iscsi_proxy)
  status = rebased_lun.rebase(src_lun)
elif options.action == 'snapshot':
  debug(1,"Doing a LUN snapshot...")
  lun = LUN(args[1],size=args[2],proxy=iscsi_proxy)
  snapshot_lun = LUN(args[0],proxy=iscsi_proxy)
  # Only the last error is returned
  status = lun.snapshot(snapshot_lun)
  status = snapshot_lun.map()
else:
  abort ("Internal error: unimplemented action (%s)" % (options.action))
  
sys.exit(status)
