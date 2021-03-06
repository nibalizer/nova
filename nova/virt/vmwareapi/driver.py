# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2011 Citrix Systems, Inc.
# Copyright 2011 OpenStack LLC.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
A connection to the VMware ESX platform.

**Related Flags**

:vmwareapi_host_ip:        IPAddress of VMware ESX server.
:vmwareapi_host_username:  Username for connection to VMware ESX Server.
:vmwareapi_host_password:  Password for connection to VMware ESX Server.
:vmwareapi_task_poll_interval:  The interval (seconds) used for polling of
                             remote tasks
                             (default: 1.0).
:vmwareapi_api_retry_count:  The API retry count in case of failure such as
                             network failures (socket errors etc.)
                             (default: 10).

"""

import time

from eventlet import event

from nova import exception
from nova.openstack.common import cfg
from nova.openstack.common import log as logging
from nova import utils
from nova.virt import driver
from nova.virt.vmwareapi import error_util
from nova.virt.vmwareapi import vim
from nova.virt.vmwareapi import vim_util
from nova.virt.vmwareapi import vmops
from nova.virt.vmwareapi import volumeops


LOG = logging.getLogger(__name__)

vmwareapi_opts = [
    cfg.StrOpt('vmwareapi_host_ip',
               default=None,
               help='URL for connection to VMware ESX host. Required if '
                    'compute_driver is vmwareapi.VMwareESXDriver.'),
    cfg.StrOpt('vmwareapi_host_username',
               default=None,
               help='Username for connection to VMware ESX host. '
                    'Used only if compute_driver is '
                    'vmwareapi.VMwareESXDriver.'),
    cfg.StrOpt('vmwareapi_host_password',
               default=None,
               help='Password for connection to VMware ESX host. '
                    'Used only if compute_driver is '
                    'vmwareapi.VMwareESXDriver.'),
    cfg.FloatOpt('vmwareapi_task_poll_interval',
                 default=5.0,
                 help='The interval used for polling of remote tasks. '
                       'Used only if compute_driver is '
                       'vmwareapi.VMwareESXDriver.'),
    cfg.IntOpt('vmwareapi_api_retry_count',
               default=10,
               help='The number of times we retry on failures, e.g., '
                    'socket error, etc. '
                    'Used only if compute_driver is '
                    'vmwareapi.VMwareESXDriver.'),
    ]

CONF = cfg.CONF
CONF.register_opts(vmwareapi_opts)

TIME_BETWEEN_API_CALL_RETRIES = 2.0


class Failure(Exception):
    """Base Exception class for handling task failures."""

    def __init__(self, details):
        self.details = details

    def __str__(self):
        return str(self.details)


class VMwareESXDriver(driver.ComputeDriver):
    """The ESX host connection object."""

    def __init__(self, virtapi, read_only=False, scheme="https"):
        super(VMwareESXDriver, self).__init__(virtapi)

        host_ip = CONF.vmwareapi_host_ip
        host_username = CONF.vmwareapi_host_username
        host_password = CONF.vmwareapi_host_password
        api_retry_count = CONF.vmwareapi_api_retry_count
        if not host_ip or host_username is None or host_password is None:
            raise Exception(_("Must specify vmwareapi_host_ip,"
                              "vmwareapi_host_username "
                              "and vmwareapi_host_password to use"
                              "compute_driver=vmwareapi.VMwareESXDriver"))

        self._session = VMwareAPISession(host_ip, host_username, host_password,
                                   api_retry_count, scheme=scheme)
        self._volumeops = volumeops.VMwareVolumeOps(self._session)
        self._vmops = vmops.VMwareVMOps(self._session)

    def init_host(self, host):
        """Do the initialization that needs to be done."""
        # FIXME(sateesh): implement this
        pass

    def legacy_nwinfo(self):
        return True

    def list_instances(self):
        """List VM instances."""
        return self._vmops.list_instances()

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        """Create VM instance."""
        self._vmops.spawn(context, instance, image_meta, network_info)

    def snapshot(self, context, instance, name, update_task_state):
        """Create snapshot from a running VM instance."""
        self._vmops.snapshot(context, instance, name, update_task_state)

    def reboot(self, instance, network_info, reboot_type,
               block_device_info=None):
        """Reboot VM instance."""
        self._vmops.reboot(instance, network_info)

    def destroy(self, instance, network_info, block_device_info=None,
                destroy_disks=True):
        """Destroy VM instance."""
        self._vmops.destroy(instance, network_info, destroy_disks)

    def pause(self, instance):
        """Pause VM instance."""
        self._vmops.pause(instance)

    def unpause(self, instance):
        """Unpause paused VM instance."""
        self._vmops.unpause(instance)

    def suspend(self, instance):
        """Suspend the specified instance."""
        self._vmops.suspend(instance)

    def resume(self, instance, network_info, block_device_info=None):
        """Resume the suspended VM instance."""
        self._vmops.resume(instance)

    def get_info(self, instance):
        """Return info about the VM instance."""
        return self._vmops.get_info(instance)

    def get_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        return self._vmops.get_info(instance)

    def get_console_output(self, instance):
        """Return snapshot of console."""
        return self._vmops.get_console_output(instance)

    def get_volume_connector(self, instance):
        """Return volume connector information."""
        return self._volumeops.get_volume_connector(instance)

    def attach_volume(self, connection_info, instance, mountpoint):
        """Attach volume storage to VM instance."""
        return self._volumeops.attach_volume(connection_info,
                                             instance,
                                             mountpoint)

    def detach_volume(self, connection_info, instance, mountpoint):
        """Detach volume storage to VM instance."""
        return self._volumeops.detach_volume(connection_info,
                                             instance,
                                             mountpoint)

    def get_console_pool_info(self, console_type):
        """Get info about the host on which the VM resides."""
        return {'address': CONF.vmwareapi_host_ip,
                'username': CONF.vmwareapi_host_username,
                'password': CONF.vmwareapi_host_password}

    def get_available_resource(self, nodename):
        """This method is supported only by libvirt."""
        return

    def plug_vifs(self, instance, network_info):
        """Plug VIFs into networks."""
        self._vmops.plug_vifs(instance, network_info)

    def unplug_vifs(self, instance, network_info):
        """Unplug VIFs from networks."""
        self._vmops.unplug_vifs(instance, network_info)


class VMwareAPISession(object):
    """
    Sets up a session with the ESX host and handles all
    the calls made to the host.
    """

    def __init__(self, host_ip, host_username, host_password,
                 api_retry_count, scheme="https"):
        self._host_ip = host_ip
        self._host_username = host_username
        self._host_password = host_password
        self.api_retry_count = api_retry_count
        self._scheme = scheme
        self._session_id = None
        self.vim = None
        self._create_session()

    def _get_vim_object(self):
        """Create the VIM Object instance."""
        return vim.Vim(protocol=self._scheme, host=self._host_ip)

    def _create_session(self):
        """Creates a session with the ESX host."""
        while True:
            try:
                # Login and setup the session with the ESX host for making
                # API calls
                self.vim = self._get_vim_object()
                session = self.vim.Login(
                               self.vim.get_service_content().sessionManager,
                               userName=self._host_username,
                               password=self._host_password)
                # Terminate the earlier session, if possible ( For the sake of
                # preserving sessions as there is a limit to the number of
                # sessions we can have )
                if self._session_id:
                    try:
                        self.vim.TerminateSession(
                                self.vim.get_service_content().sessionManager,
                                sessionId=[self._session_id])
                    except Exception, excep:
                        # This exception is something we can live with. It is
                        # just an extra caution on our side. The session may
                        # have been cleared. We could have made a call to
                        # SessionIsActive, but that is an overhead because we
                        # anyway would have to call TerminateSession.
                        LOG.debug(excep)
                self._session_id = session.key
                return
            except Exception, excep:
                LOG.critical(_("In vmwareapi:_create_session, "
                              "got this exception: %s") % excep)
                raise exception.NovaException(excep)

    def __del__(self):
        """Logs-out the session."""
        # Logout to avoid un-necessary increase in session count at the
        # ESX host
        try:
            self.vim.Logout(self.vim.get_service_content().sessionManager)
        except Exception, excep:
            # It is just cautionary on our part to do a logout in del just
            # to ensure that the session is not left active.
            LOG.debug(excep)

    def _is_vim_object(self, module):
        """Check if the module is a VIM Object instance."""
        return isinstance(module, vim.Vim)

    def _call_method(self, module, method, *args, **kwargs):
        """
        Calls a method within the module specified with
        args provided.
        """
        args = list(args)
        retry_count = 0
        exc = None
        last_fault_list = []
        while True:
            try:
                if not self._is_vim_object(module):
                    # If it is not the first try, then get the latest
                    # vim object
                    if retry_count > 0:
                        args = args[1:]
                    args = [self.vim] + args
                retry_count += 1
                temp_module = module

                for method_elem in method.split("."):
                    temp_module = getattr(temp_module, method_elem)

                return temp_module(*args, **kwargs)
            except error_util.VimFaultException, excep:
                # If it is a Session Fault Exception, it may point
                # to a session gone bad. So we try re-creating a session
                # and then proceeding ahead with the call.
                exc = excep
                if error_util.FAULT_NOT_AUTHENTICATED in excep.fault_list:
                    # Because of the idle session returning an empty
                    # RetrievePropertiesResponse and also the same is returned
                    # when there is say empty answer to the query for
                    # VMs on the host ( as in no VMs on the host), we have no
                    # way to differentiate.
                    # So if the previous response was also am empty response
                    # and after creating a new session, we get the same empty
                    # response, then we are sure of the response being supposed
                    # to be empty.
                    if error_util.FAULT_NOT_AUTHENTICATED in last_fault_list:
                        return []
                    last_fault_list = excep.fault_list
                    self._create_session()
                else:
                    # No re-trying for errors for API call has gone through
                    # and is the caller's fault. Caller should handle these
                    # errors. e.g, InvalidArgument fault.
                    break
            except error_util.SessionOverLoadException, excep:
                # For exceptions which may come because of session overload,
                # we retry
                exc = excep
            except Exception, excep:
                # If it is a proper exception, say not having furnished
                # proper data in the SOAP call or the retry limit having
                # exceeded, we raise the exception
                exc = excep
                break
            # If retry count has been reached then break and
            # raise the exception
            if retry_count > self.api_retry_count:
                break
            time.sleep(TIME_BETWEEN_API_CALL_RETRIES)

        LOG.critical(_("In vmwareapi:_call_method, "
                     "got this exception: %s") % exc)
        raise

    def _get_vim(self):
        """Gets the VIM object reference."""
        if self.vim is None:
            self._create_session()
        return self.vim

    def _wait_for_task(self, instance_uuid, task_ref):
        """
        Return a Deferred that will give the result of the given task.
        The task is polled until it completes.
        """
        done = event.Event()
        loop = utils.FixedIntervalLoopingCall(self._poll_task, instance_uuid,
                                              task_ref, done)
        loop.start(CONF.vmwareapi_task_poll_interval)
        ret_val = done.wait()
        loop.stop()
        return ret_val

    def _poll_task(self, instance_uuid, task_ref, done):
        """
        Poll the given task, and fires the given Deferred if we
        get a result.
        """
        try:
            task_info = self._call_method(vim_util, "get_dynamic_property",
                            task_ref, "Task", "info")
            task_name = task_info.name
            if task_info.state in ['queued', 'running']:
                return
            elif task_info.state == 'success':
                LOG.debug(_("Task [%(task_name)s] %(task_ref)s "
                            "status: success") % locals())
                done.send("success")
            else:
                error_info = str(task_info.error.localizedMessage)
                LOG.warn(_("Task [%(task_name)s] %(task_ref)s "
                          "status: error %(error_info)s") % locals())
                done.send_exception(exception.NovaException(error_info))
        except Exception, excep:
            LOG.warn(_("In vmwareapi:_poll_task, Got this error %s") % excep)
            done.send_exception(excep)
