#!/usr/bin/env python
import os
import re
import traceback
import string
import subprocess
import sys
import urllib
import urlparse
from xml.etree import ElementTree as ET

import click

from bioblend import toolshed


TOOLSHED = "https://toolshed.g2.bx.psu.edu"
TOOLSHED_MAP = {
    "toolshed": "https://toolshed.g2.bx.psu.edu",
    "testtoolshed": "https://testtoolshed.g2.bx.psu.edu",
}
GIT_USER = "jmchilton"
if sys.platform == "darwin":
    DEFAULT_HOMEBREW_ROOT = "/usr/local"
else:
    DEFAULT_HOMEBREW_ROOT = os.path.join(os.path.expanduser("~"), ".linuxbrew")


EXTENSION_ENVIRONMENT = """
def environment(actions)
    # Setup envirnoment variable modifications that will be used later by
    # platform-brew's env and vinstall commands.
    act_hash = {"actions" => actions}
    (prefix / "platform_environment.json").write act_hash.to_json
end
"""


@click.command()
@click.option('--tool_shed', default="toolshed", type=click.Choice(TOOLSHED_MAP.keys()), help='Tool shed to target.')
@click.option('--owner', default=None, help='Limit generation to specific owner.')
@click.option('--name_filter', default=None, help='Apply regex to name filters.')
@click.option('--git_user', default="jmchilton")
@click.option('--brew_directory', default=DEFAULT_HOMEBREW_ROOT)
def main(**kwds):
    user = kwds["git_user"]
    repo_name = "homebrew-%s" % kwds["tool_shed"]
    target = os.path.join(kwds["brew_directory"], "Library", "Taps", user, repo_name )

    tap = Tap("%s/%s" % (user, kwds["tool_shed"]))
    shell("rm -rf %s" % target)
    shell("mkdir -p %s" % target)
    prefix = kwds["tool_shed"]
    tool_shed_url = TOOLSHED_MAP[prefix]
    dependencies_list = []
    for raw_repo in repos(tool_shed_url, owner=kwds["owner"], name_filter=kwds["name_filter"]):
        repo = Repo.from_api(prefix, raw_repo)
        dependencies_file = repo.get_file("tool_dependencies.xml")
        if not dependencies_file:
            click.echo("skipping repository %s, no tool_dependencies.xml" % repo)
            continue
        try:
            dependencies = Dependencies(dependencies_file, repo, tap)
        except Exception as e:
            print "Failed to parse dependencies for repo %s, skipping." % repo
            continue
        dependencies_list.append(dependencies)

    for dependencies in dependencies_list:
        for package in dependencies.packages:
            try:
                (file_name, contents) = package.to_recipe()
                recipe_path = os.path.join(target, file_name)
                open(recipe_path, "w").write(contents)
            except Exception as e:
                traceback.print_exc()
                print "Failed to convert package [%s], exception [%s]" % (package, e)
            #print "Recipe in file: %s" % file
            #print "=" * 79
            #print contents
            #print "=" * 79
        #for dependency in dependencies.dependencies:
        #    print dependency

    shell("git init %s" % target)
    shell("git --work-tree %s --git-dir %s/.git add %s/*" % (target, target, target))
    shell("git --work-tree %s --git-dir %s/.git commit -m 'Initial Commit' " % (target, target))


class Tap(object):

    def __init__(self, prefix):
        self.prefix = prefix


class Dependencies(object):

    def __init__(self, dependencies_file, repo, tap):
        self.repo = repo
        self.tap = tap
        self.root = ET.parse(dependencies_file).getroot()
        packages = []
        dependencies = []
        package_els = self.root.findall("package")
        if not package_els:
            print "No packages found for repo %s" % repo
        for package_el in package_els:
            install_els = package_el.findall("install")
            assert len(install_els) in (0, 1)
            if len(install_els) == 1:
                install_el = install_els[0]
                packages.append(Package(self, package_el, install_el))
            else:
                repository_el = package_el.find("repository")
                assert repository_el is not None, "no repository in package el for %s" % repo
                dependencies.append(Dependency(self, package_el, repository_el))

        self.packages = packages
        self.dependencies = dependencies

    def single_package(self):
        return len(self.packages) == 1

    def __repr__(self):
        return "Dependencies[for_repo=%s]" % self.repo


class Dependency(object):

    def __init__(self, dependencies, package_el, repository_el):
        self.dependencies = dependencies
        self.package_el = package_el
        self.repository_el = repository_el
        self.repo = Repo.from_xml(repository_el)

    def __repr__(self):
        return "Dependency[package_name=%s,version=%s,dependent_package=%s]" % (self.package_el.attrib["name"], self.package_el.attrib["version"], self.repository_el.attrib["name"])


class Actions(object):

    def __init__(self, actions, os=None, architecture=None, action_packages=[]):
        self.os = os
        self.architecture = architecture
        self.actions = actions or []
        self.action_packages = action_packages

    def first_download(self):
        for action in self.actions:
            if action.type in ["download_by_url", "download_file"]:
                return action
        return None

    def downloads(self):
        actions = []
        for action in self.actions:
            if action.type in ["download_by_url", "download_file"]:
                actions.append(action)
        return actions

    def __repr__(self):
        platform = ""
        if self.os or self.architecture:
            platform = "os=%s,arch=%s," % (self.os, self.architecture)
        return "Actions[%s%s]" % (platform, map(str, self.actions))


class Action(object):

    def __init__(self, **kwds):
        self._keys = []
        for key, value in kwds.iteritems():
            self._keys.append(key)
            setattr(self, key, value)

    def __repr__(self):
        return "Action[type=%s]" % self.type

    def same_as(self, other):
        if self._keys != other._keys:
            return False
        else:
            for key in self._keys:
                if getattr(self, key) != getattr(other, key):
                    return False

            return True

    def to_ruby(self):
        action_type = self.type
        statements = []
        if action_type == "shell_command":
            command = self.command.strip()
            if "\n" in command:
                command = templatize_string(command)
                command = "\n".join([p.strip() for p in command.split("\n") if p])
                statements.append('''system <<-EOF\n%s\nEOF''' % command)
            else:
                statements.append('''system %s ''' % shell_string(self.command))
        elif action_type == "move_file":
            named_destination = self.named_dir(self.destination)
            if named_destination:
                statements.append('''%s.install %s''' % (named_destination, shell_string(self.source)))
            else:
                statements.append('''system "mkdir", "-fp", %s''' % shell_string(self.destination))
                statements.append('''system "mv", %s, %s''' % (shell_string(self.source), shell_string(self.destination)))
        elif action_type == "move_directory_files":
            named_destination = self.named_dir(self.destination_directory)
            if named_destination:
                statements.append('''%s.install Dir["%s/*"]''' % (named_destination, shell_string(self.source_directory, quote_now=False)))
            else:
                statements.append('''system "mkdir", "-fp", %s''' % shell_string(self.destination_directory))
                statements.append('''system "mv "%s/*" %s ''' % (shell_string(self.source_directory, quote_now=False), shell_string(self.destination_directory)))
        elif action_type == "set_environment":
            modify_environment = []
            for variable in self.variables:
                if variable.explicit:
                    modify_environment.append(variable)
                else:
                    statements.append("# Tool Shed set environment variable that is picked implicitly.")
            if modify_environment:
                list_str = '''['''
                for i, set_variable in enumerate(modify_environment):
                    if i > 0:
                        list_str += ","
                    list_str += set_variable.to_ruby_hash()
                list_str += ']'
                if self.package.has_multiple_set_environments():
                    statements.append('''environment_actions += %s''' % list_str)
                else:
                    statements.append('''environment(%s)''' % list_str)
                self.package.extensions_used.add('ENVIRONMENT')
        elif action_type == "chmod":
            for mod in self.mods:
                target = shell_string(variable["target"])
                statements.append('''system "chmod", "%s", %s''', (mod["mode"], target))
        elif action_type == "make_install":
            statements.append('''system "make install"''')
        elif action_type == "download_file":
            resource = url_to_resource(self.text)
            if self.extract:
                statements.append("resource('%s').stage do" % resource)
                statements.append('''    # Tool Shed would download inside build directory instead of its own- so move it.''')
                statements.append('''    buildpath.install Dir["../*"]''')
                statements.append("end")
            else:
                statements.append("# download file without extract - already available?")
        elif action_type == "change_directory":
            statements.append("cd '%s'" % self.directory)
        elif action_type == "make_directory":
            statements.append('''system "mkdir", "-fp", %s''' % shell_string(self.directory))
        else:
            statements.append(self.RAW_RUBY)

        return statements

    def named_dir(self, path):
        ruby_path = shell_string(path, quote_now=False)
        if ruby_path == "#{prefix}":
            return "prefix"
        elif ruby_path == "#{prefix}/bin":
            return "bin"
        else:
            return None

    def explicit_variables(self):
        type = self.type
        if type == "set_environment":
            return filter(lambda v: v.explicit, self.variables)
        else:
            return []

    @classmethod
    def from_elem(clazz, elem, package):
        type = elem.attrib["type"]
        kwds = {}
        if type == "download_by_url":
            kwds["text"] = elem.text
        elif type == "download_file":
            kwds["text"] = elem.text
            kwds["extract"] = elem.attrib.get("extract", False)
        elif type == "shell_command":
            kwds["command"] = elem.text
        elif type == "move_file":
            kwds["source"] = elem.find("source").text
            kwds["destination"] = elem.find("destination").text
        elif type == "move_directory_files":
            kwds["source_directory"] = elem.find("source_directory").text
            kwds["destination_directory"] = elem.find("destination_directory").text
        elif type == "set_environment":
            variables = []
            for ev_elem in elem.findall("environment_variable"):
                var = SetVariable(ev_elem)
                variables.append(var)
            kwds["variables"] = variables
        elif type == "chmod":
            mods = []
            for mod_elem in elem.findall("file"):
                mod = {}
                mod["mode"] = mod_elem.attrib["mode"]
                mod["target"] = mod_elem.text
            kwds["mods"] = mods
        elif type == "make_install":
            pass
        elif type == "download_file":
            pass
        elif type == "change_directory":
            kwds["directory"] = elem.text
        elif type == "make_directory":
            kwds["directory"] = elem.text
        elif type == "set_environment_for_install":
            kwds["RAW_RUBY"] = "# Skipping set_environment_for_install command, handled by platform brew."
        else:
            kwds["RAW_RUBY"] = '''onoe("Unhandled tool shed action [%s] encountered.")''' % type

        return Action(type=type, package=package, **kwds)


class SetVariable(object):
    
    def __init__(self, elem):
        self.action = elem.attrib["action"]
        self.name = elem.attrib["name"]
        self.raw_value = elem.text
        self.ruby_value = templatize_string(self.raw_value)

    @property
    def explicit(self):
        return not self.implicit

    @property
    def implicit(self):
        if self.name == "PATH" and self.ruby_value == "#{prefix}/bin":
            return True
        else:
            return False

    def to_ruby_hash(self):
        action = self.action
        variable = self.name
        value = self.ruby_value.replace("#{prefix}", "$KEG_ROOT")
        if action == "set_to":
            action = "set"
        elif action == "prepend_to":
            action = "prepend"
        else:
            action = "append"
        return '''{'action'=> '%s', 'variable'=> '%s', 'value'=> '%s'}''' % (action, variable, value)


def shell_string(tool_shed_str, quote_now=True, templatize=True):
    if templatize:
        target_string = templatize_string(tool_shed_str)
    else:
        target_string = tool_shed_str.replace("#", "\\#")
    to_ruby = (target_string.replace('"', '\\"'))
    if quote_now:
        return '"%s"' % to_ruby
    else:
        return to_ruby


def templatize_string(tool_shed_str):
    tool_shed_str.replace("#", "\\#")
    env_var_dict = {}
    env_var_dict[ 'INSTALL_DIR' ] = '#{prefix}'
    env_var_dict[ 'system_install' ] = '#{prefix}'
    # If the Python interpreter is 64bit then we can safely assume that the underlying system is also 64bit.
    env_var_dict[ '__is64bit__' ] = '#{Hardware.Hardware.is_64_bit?}'
    return string.Template(tool_shed_str).safe_substitute(env_var_dict)


class Package(object):

    def __init__(self, dependencies, package_el, install_el):
        self.dependencies = dependencies
        self.package_el = package_el
        self.install_el = install_el
        self.extensions_used = set()
        self.all_actions = self.get_all_actions()
        self.no_arch_option = self.has_no_achitecture_install()

    def get_all_actions(self):
        action_or_group = self.install_el[0]
        parsed_actions = []
        if action_or_group.tag == "actions":
            parsed_actions.append(self.parse_actions(action_or_group))
        elif action_or_group.tag == "actions_group":
            for actions in action_or_group.findall("actions"):
                parsed_actions.append(self.parse_actions(actions))
            for action in action_or_group.findall("action"):
                for parsed_a in parsed_actions:
                    parsed_a.actions.append(self.parse_action(action))
        return parsed_actions

    def to_recipe(self):
        name = self.get_recipe_name()
        formula_builder = FormulaBuilder()
        if self.has_explicit_set_environments():
            # Required for environment method.
            formula_builder.require('json')

        name = name.replace("__", "_")
        parts = [p[0].upper() + p[1:] for p in name.split("__")]
        temp = "|".join(parts)
        parts = [p[0].upper() + p[1:] for p in temp.split("_")]
        class_name = "".join(parts).replace("|", "_")
        formula_builder.set_class_name(class_name)
        formula_builder.add_line('''option "without-architecture", "Build without allowing architecture information (to force source install when binaries are available)."''')
        self.pop_download_block(formula_builder)
        self.pop_deps(formula_builder)
        self.pop_install_def(formula_builder)
        self.pop_extensions(formula_builder)
        formula_builder.finish_formula()
        return "%s.rb" % name, formula_builder.to_file()

    def get_recipe_name(self):
        repo = self.dependencies.repo
        base = repo.recipe_base_name()
        if self.dependencies.single_package():
            return base
        else:
            return base + self.package_el.attrib["name"]

    def pop_install_def(self, formula_builder):
        formula_builder.add_and_indent("def install")
        multiple_set_environments = self.has_multiple_set_environments()
        if multiple_set_environments:
            formula_builder.add_line("environment_actions = []")

        def handle_actions(actions):
            return self.populate_actions(formula_builder, actions.actions[1:])

        if self.actions_diff_only_by_download():
            handle_actions(self.all_actions[0])
        else:
            self.conditional_action_map(formula_builder, handle_actions)

        if multiple_set_environments:
            formula_builder.add_line('''environment(environment_actions)''')

        formula_builder.end()

    def pop_deps(self, formula_builder):
        def handle_actions(actions):
            return self.populate_actions_packages(formula_builder, actions.action_packages)

        self.populate_actions_packages(formula_builder, self.dependencies.dependencies)
        self.conditional_action_map(formula_builder, handle_actions)

    def pop_extensions(self, formula_builder):
        for extension in self.extensions_used:
            map(formula_builder.add_line, globals()["EXTENSION_%s" % extension].split("\n"))

    def populate_actions_packages(self, formula_builder, packages):
        for package in packages:
            repo = package.repo
            prefix = self.dependencies.tap.prefix
            base = "%s/%s" % (prefix, repo.recipe_base_name())
            formula_builder.add_line('depends_on "%s"' % base)

    def populate_actions(self, formula_builder, actions):
        for action in actions:
            for line in action.to_ruby():
                formula_builder.add_line(line)

    def actions_diff_only_by_download(self):
        all_actions = self.all_actions
        first_actions = all_actions[0].actions
        for actions in all_actions[1:]:
            if len(first_actions) != len(actions.actions):
                return False
            for i, action in enumerate(actions.actions):
                if action.type in ["download_by_url", "download_file"] and first_actions[0].type == action.type:
                    continue
                else:
                    if not action.same_as(first_actions[i]):
                        return False
        return True

    def pop_download_block(self, formula_builder):
        def func(actions):
            self.pop_download(actions, formula_builder)

        self.conditional_action_map(formula_builder, func)

    def conditional_action_map(self, formula_builder, func):
        all_actions = self.all_actions
        if len(all_actions) == 1:
            func(all_actions[0])
        else:
            for i, actions in enumerate(all_actions):
                if i > 0:
                    formula_builder.unindent()
                conds = []
                if actions.os and actions.os == "linux":
                    conds.append("OS.linux?")
                elif actions.os and actions.os == "darwin":
                    conds.append("OS.mac?")
                if actions.architecture and actions.architecture == "x86_64":
                    conds.append("Hardware.is_64_bit?")
                elif actions.architecture and actions.architecture == "i386":
                    conds.append("Hardware.is_32_bit?")
                if conds and self.no_arch_option:
                    conds.append('!build.without?("architecture")')
                conds_str = " and ".join(conds)
                if not conds_str:
                    assert i == len(all_actions) - 1, actions
                    formula_builder.add_and_indent("else")
                    func(actions)
                else:
                    cond_op = "%sif" % ("" if i == 0 else "els")
                    formula_builder.add_and_indent("%s %s" % (cond_op, conds_str))
                    func(actions)
            formula_builder.end()

    def has_no_achitecture_install(self):
        all_actions = self.all_actions
        if len(all_actions) < 2:
            return False
        else:
            last_action = all_actions[-1]
            return (not last_action.architecture) and (not last_action.os)

    def has_explicit_set_environments(self):
        all_actions = self.all_actions
        for actions in all_actions:
            for action in actions.actions:
                if action.explicit_variables:
                    return True
        return False

    def has_multiple_set_environments(self):
        all_actions = self.all_actions
        for actions in all_actions:
            count = 0
            for action in actions.actions:
                if action.explicit_variables:
                    count += 1
            if count > 1:
                return True
        return False

    def pop_download(self, actions, formula_builder):
        one_populated = False
        for action in actions.downloads():
            if one_populated:
                resource = url_to_resource(action.text)
                formula_builder.add_and_indent("resource '%s' do" % resource)
                self.pop_single_download(action, formula_builder)
                formula_builder.end()
            else:
                self.pop_single_download(action, formula_builder)
            one_populated = True
        if not one_populated:
            self.pop_single_download(None, formula_builder)

    def pop_single_download(self, action, formula_builder):
        if action is None:
            url = "http://ftpmirror.gnu.org/hello/hello-2.9.tar.gz"
            sha1 = "cb0470b0e8f4f7768338f5c5cfe1688c90fbbc74"
        else:
            url = action.text
            sha1 = self.fetch_sha1(url)
        download_line = '''url "%s"''' % url
        if action and action.type == "download_file" and not action.extract:
            download_line += ", :using => :nounzip"
        formula_builder.add_line(download_line)
        formula_builder.add_line('''sha1 "%s"''' % sha1)

    def fetch_sha1(self, url):
        return ''  # TODO

    def parse_actions(self, actions):
        os = actions.attrib.get("os", None)
        architecture = actions.get("architecture", None)
        parsed_actions = map(self.parse_action, actions.findall("action"))
        action_packages = []
        for package in actions.findall("package"):
            action_packages.append(self.parse_action_package(package))
        return Actions(parsed_actions, os, architecture, action_packages)

    def parse_action_package(self, elem):
        name = elem.attrib["name"]
        version = elem.attrib["version"]
        repo = Repo.from_xml(elem.find("repository"))
        return ActionPackage(name, version, repo)

    def parse_action(self, action):
        return Action.from_elem(action, package=self)

    def __repr__(self):
        actions = self.all_actions
        parts = (self.package_el.attrib["name"], self.package_el.attrib["version"], self.dependencies, actions)
        return "Install[name=%s,version=%s,dependencies=%s,actions=%s]" % parts


class ActionPackage(object):

    def __init__(self, name, version, repo):
        self.name = name
        self.version = version
        self.repo = repo


class Repo(object):

    def __init__(self, **kwds):
        for key, value in kwds.iteritems():
            setattr(self, key, value)

    def recipe_base_name(self):
        owner = self.owner.replace("-", "")
        name = self.name
        name = name.replace("_", "").replace("-", "")
        base = "%s_%s" % (owner, name)
        return base

    @staticmethod
    def from_xml(elem):
        tool_shed_url = elem.attrib["toolshed"]
        if "testtoolshed" in tool_shed_url:
            prefix = "testtoolshed"
        else:
            prefix = "toolshed"
        return Repo(
            prefix=prefix,
            name=elem.attrib["name"],
            owner=elem.attrib["owner"],
            tool_shed_url=tool_shed_url,
            changeset_revision=elem.attrib["changeset_revision"],
            prior_installation_required=elem.attrib["prior_installation_required"],
        )

    @staticmethod
    def from_api(prefix, repo_json):
        return Repo(
            prefix=prefix,
            name=repo_json["name"],
            owner=repo_json["owner"],
            tool_shed_url=TOOLSHED_MAP[prefix],
        )

    def get_file(self, path):
        try:
            url = "%s/repos/%s/%s/raw-file/tip/%s" % (self.tool_shed_url, self.owner, self.name, path)
            path, headers = urllib.urlretrieve(url)
            return path
        except Exception as e:
            print e
            return None

    def __repr__(self):
        return "Repository[name=%s,owner=%s]" % (self.name, self.owner)


def url_to_resource(url):
    path = urlparse.urlparse(url).path
    name = os.path.split(path)[1]
    base = name.rstrip("\.tar\.gz").rstrip("\.zip")
    return base


class RubyBuilder(object):

    def __init__(self):
        self.lines = []
        self.indent = 0

    def add_line(self, line):
        indent_spaces = "  " * self.indent
        self.lines.append("%s%s" % (indent_spaces, line))

    def add_and_indent(self, line):
        self.add_line(line)
        self.indent += 1

    def end(self):
        self.unindent()
        self.add_line("end")

    def unindent(self):
        self.indent -= 1

    def to_file(self):
        assert self.indent == 0, "\n".join(self.lines)
        return "\n".join(self.lines)

    def require(self, module):
        assert self.indent == 0
        self.add_line("require '%s'" % module)


class FormulaBuilder(RubyBuilder):

    def __init__(self):
        super(FormulaBuilder, self).__init__()
        self.require('formula')

    def set_class_name(self, name):
        self.add_line("")
        self.add_and_indent("class %s < Formula" % name)
        self.add_line('version "1.0"')

    def finish_formula(self):
        self.end()


def shell(cmds, **popen_kwds):
    click.echo(cmds)
    p = subprocess.Popen(cmds, shell=True, **popen_kwds)
    return p.wait()


def repos(tool_shed_url, name_filter=None, owner=None):
    ts = toolshed.ToolShedInstance(url=TOOLSHED)
    repos = ts.repositories.get_repositories()
    if owner:
        repos = [r for r in repos if r["owner"] == owner]
    if name_filter:
        pattern = re.compile(name_filter)
        repos = [r for r in repos if pattern.match(r["name"])]
    return repos


if __name__ == "__main__":
    main()
