# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
#    Copyright (C) 2012 Red Hat, Inc.
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

import sys

from nova import test
from nova.tests import fakeguestfs
from nova.virt.disk import api as diskapi
from nova.virt.disk.vfs import guestfs as vfsguestfs


class VirtDiskTest(test.TestCase):

    def setUp(self):
        super(VirtDiskTest, self).setUp()
        sys.modules['guestfs'] = fakeguestfs
        vfsguestfs.guestfs = fakeguestfs

    def test_inject_data_key(self):

        vfs = vfsguestfs.VFSGuestFS("/some/file", "qcow2")
        vfs.setup()

        diskapi._inject_key_into_fs("mysshkey", vfs)

        self.assertTrue("/root/.ssh" in vfs.handle.files)
        self.assertEquals(vfs.handle.files["/root/.ssh"],
                          {'isdir': True, 'gid': 0, 'uid': 0, 'mode': 0700})
        self.assertTrue("/root/.ssh/authorized_keys" in vfs.handle.files)
        self.assertEquals(vfs.handle.files["/root/.ssh/authorized_keys"],
                          {'isdir': False,
                           'content': "Hello World\n# The following ssh " +
                                      "key was injected by Nova\nmysshkey\n",
                           'gid': 100,
                           'uid': 100,
                           'mode': 0700})

        vfs.teardown()

    def test_inject_data_key_with_selinux(self):

        vfs = vfsguestfs.VFSGuestFS("/some/file", "qcow2")
        vfs.setup()

        vfs.make_path("etc/selinux")
        vfs.make_path("etc/rc.d")
        diskapi._inject_key_into_fs("mysshkey", vfs)

        self.assertTrue("/etc/rc.d/rc.local" in vfs.handle.files)
        self.assertEquals(vfs.handle.files["/etc/rc.d/rc.local"],
                          {'isdir': False,
                           'content': "Hello World#!/bin/sh\n# Added by " +
                                      "Nova to ensure injected ssh keys " +
                                      "have the right context\nrestorecon " +
                                      "-RF root/.ssh 2>/dev/null || :\n",
                           'gid': 100,
                           'uid': 100,
                           'mode': 0700})

        self.assertTrue("/root/.ssh" in vfs.handle.files)
        self.assertEquals(vfs.handle.files["/root/.ssh"],
                          {'isdir': True, 'gid': 0, 'uid': 0, 'mode': 0700})
        self.assertTrue("/root/.ssh/authorized_keys" in vfs.handle.files)
        self.assertEquals(vfs.handle.files["/root/.ssh/authorized_keys"],
                          {'isdir': False,
                           'content': "Hello World\n# The following ssh " +
                                      "key was injected by Nova\nmysshkey\n",
                           'gid': 100,
                           'uid': 100,
                           'mode': 0700})

        vfs.teardown()

    def test_inject_data_key_with_selinux_append_with_newline(self):

        vfs = vfsguestfs.VFSGuestFS("/some/file", "qcow2")
        vfs.setup()

        vfs.replace_file("/etc/rc.d/rc.local", "#!/bin/sh\necho done")
        vfs.make_path("etc/selinux")
        vfs.make_path("etc/rc.d")
        diskapi._inject_key_into_fs("mysshkey", vfs)

        self.assertTrue("/etc/rc.d/rc.local" in vfs.handle.files)
        self.assertEquals(vfs.handle.files["/etc/rc.d/rc.local"],
                {'isdir': False,
                 'content': "#!/bin/sh\necho done\n# Added "
                            "by Nova to ensure injected ssh keys have "
                            "the right context\nrestorecon -RF "
                            "root/.ssh 2>/dev/null || :\n",
                 'gid': 100,
                 'uid': 100,
                 'mode': 0700})
        vfs.teardown()

    def test_inject_net(self):

        vfs = vfsguestfs.VFSGuestFS("/some/file", "qcow2")
        vfs.setup()

        diskapi._inject_net_into_fs("mynetconfig", vfs)

        self.assertTrue("/etc/network/interfaces" in vfs.handle.files)
        self.assertEquals(vfs.handle.files["/etc/network/interfaces"],
                          {'content': 'mynetconfig',
                           'gid': 100,
                           'isdir': False,
                           'mode': 0700,
                           'uid': 100})
        vfs.teardown()

    def test_inject_metadata(self):
        vfs = vfsguestfs.VFSGuestFS("/some/file", "qcow2")
        vfs.setup()

        diskapi._inject_metadata_into_fs([{"key": "foo",
                                           "value": "bar"},
                                          {"key": "eek",
                                           "value": "wizz"}], vfs)

        self.assertTrue("/meta.js" in vfs.handle.files)
        self.assertEquals(vfs.handle.files["/meta.js"],
                          {'content': '{"foo": "bar", ' +
                                      '"eek": "wizz"}',
                           'gid': 100,
                           'isdir': False,
                           'mode': 0700,
                           'uid': 100})
        vfs.teardown()

    def test_inject_admin_password(self):
        vfs = vfsguestfs.VFSGuestFS("/some/file", "qcow2")
        vfs.setup()

        def fake_salt():
            return "1234567890abcdef"

        self.stubs.Set(diskapi, '_generate_salt', fake_salt)

        vfs.handle.write("/etc/shadow",
                         "root:$1$12345678$xxxxx:14917:0:99999:7:::\n" +
                         "bin:*:14495:0:99999:7:::\n" +
                         "daemon:*:14495:0:99999:7:::\n")

        vfs.handle.write("/etc/passwd",
                         "root:x:0:0:root:/root:/bin/bash\n" +
                         "bin:x:1:1:bin:/bin:/sbin/nologin\n" +
                         "daemon:x:2:2:daemon:/sbin:/sbin/nologin\n")

        diskapi._inject_admin_password_into_fs("123456", vfs)

        self.assertEquals(vfs.handle.files["/etc/passwd"],
                          {'content': "root:x:0:0:root:/root:/bin/bash\n" +
                                      "bin:x:1:1:bin:/bin:/sbin/nologin\n" +
                                      "daemon:x:2:2:daemon:/sbin:" +
                                      "/sbin/nologin\n",
                           'gid': 100,
                           'isdir': False,
                           'mode': 0700,
                           'uid': 100})
        shadow = vfs.handle.files["/etc/shadow"]

        # if the encrypted password is only 13 characters long, then
        # nova.virt.disk.api:_set_password fell back to DES.
        if len(shadow['content']) == 91:
            self.assertEquals(shadow,
                              {'content': "root:12tir.zIbWQ3c" +
                                          ":14917:0:99999:7:::\n" +
                                          "bin:*:14495:0:99999:7:::\n" +
                                          "daemon:*:14495:0:99999:7:::\n",
                               'gid': 100,
                               'isdir': False,
                               'mode': 0700,
                               'uid': 100})
        else:
            self.assertEquals(shadow,
                              {'content': "root:$1$12345678$a4ge4d5iJ5vw" +
                                          "vbFS88TEN0:14917:0:99999:7:::\n" +
                                          "bin:*:14495:0:99999:7:::\n" +
                                          "daemon:*:14495:0:99999:7:::\n",
                               'gid': 100,
                               'isdir': False,
                               'mode': 0700,
                               'uid': 100})
        vfs.teardown()
