import json
import os
from ansible.errors import AnsibleParserError
from ansible.module_utils.common.text.converters import to_bytes, to_text, to_native
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.vars import BaseVarsPlugin
from ansible.inventory.host import Host
from ansible.inventory.group import Group
from subprocess import PIPE, Popen

from ansible.utils.vars import combine_vars

DOCUMENTATION = r"""
name: onepassword
author: Hayden Young (@hayden.moe) <hayden@hayden.moe>
short_description: Loading vars from 1Password
version_added: '0.1.0'
extends_documentation_fragment:
  - ansible.builtin.vars_plugin_staging
"""

FOUND = {}
FETCHED = {}

VALID_EXTENSIONS = ('.op.yaml', '.op.yml')

class VarsModule(BaseVarsPlugin):
    def get_vars(self, loader: DataLoader, path, entities):
        if not isinstance(entities, list):
            entities = [entities]

        super(VarsModule, self).get_vars(loader, path, entities)

        data = {}
        for entity in entities:
            if isinstance(entity, Host):
                subdir = 'host_vars'
            elif isinstance(entity, Group):
                subdir = 'group_vars'
            else:
                raise AnsibleParserError("Supplied entity must be Host or Group, got %s instead" % (type(entity)))

            # avoid 'chroot' type inventory hostnames /path/to/chroot
            if not entity.name.startswith(os.path.sep):
                try:
                    found_files = []

                    b_opath = os.path.realpath(to_bytes(os.path.join(self._basedir, subdir)))
                    opath = to_text(b_opath)
                    key = '%s: %s' % (entity.name, opath)
                    self._display.vvvv("keyz: %s" % (key))
                    if key in FOUND:
                        found_files = FOUND[key]
                    else:
                        if os.path.exists(b_opath):
                            if os.path.isdir(b_opath):
                                self._display.debug(f"\tprocessing dir {opath}")
                                found_files = loader.find_vars_files(opath, entity.name, extensions=VALID_EXTENSIONS, allow_dir=False)
                                found_files.extend([file_path for file_path in loader.find_vars_files(opath, entity.name)
                                                    if any(to_text(file_path).endswith(extension) for extension in VALID_EXTENSIONS)])
                                FOUND[key] = found_files
                            else:
                                self._display.warning("Found %s that is not a directory, skipping: %s" % (subdir, opath))

                        for found in found_files:
                            self._display.v('processing %s' % found)
                            new_data = self._handle_item(
                                loader.load_from_file(found, cache='all', unsafe=True)
                            )
                            data = combine_vars(data, new_data)

                except AnsibleParserError:
                    raise
                except Exception as e:
                    raise AnsibleParserError('Unexpected error in the 1Password vars plugin: %s' % to_native(e))

        return data

    def _handle_item(self, val):
        if isinstance(val, dict):
            for key, item in val.items():
                val[key] = self._handle_item(item)
        elif isinstance (val, list):
            for key, item in enumerate(val):
                val[key] = self._handle_item(item)
        else:
            # probably a normal value
            val = self._get_value(val)

        return val

    def _get_value(self, key: str):
        self._display.v('  |> `op read` for key %s' % key)
        exit_code, output, err = self._run_command(['op', 'read', key])
        output = to_text(output)

        if output.endswith('\n'):
            output = output.rstrip()

        if err:
            self._display.warning(f'unexpected stderr:\n{to_text(err, errors='surrogate_or_strict')}')

        if exit_code != 0:
            raise AnsibleParserError(f'op exited with non-zero code ({exit_code}): {to_text(err)}')

        self._display.v('    <> `op read` successful for key %s' % key)
        return to_text(output)

    def _run_command(self, command, env=None, data=None, cwd=None):
        process = Popen(command, stdin=None if data is None else PIPE, stdout=PIPE, stderr=PIPE, cwd=cwd, env=env)
        output, err = process.communicate(input=data)
        return process.returncode, output, err
