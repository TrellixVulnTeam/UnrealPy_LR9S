"""Find modules used by a script, using introspection."""
# This module should be kept compatible with Python 2.2, see PEP 291.

from __future__ import generators
import dis
import imp
import marshal
import os
import sys
import types
import struct

if hasattr(sys.__stdout__, "newlines"):
    READ_MODE = "U"  # universal line endings
else:
    # remain compatible with Python  < 2.3
    READ_MODE = "r"

LOAD_CONST = chr(dis.opname.index('LOAD_CONST'))
IMPORT_NAME = chr(dis.opname.index('IMPORT_NAME'))
STORE_NAME = chr(dis.opname.index('STORE_NAME'))
STORE_GLOBAL = chr(dis.opname.index('STORE_GLOBAL'))
STORE_OPS = [STORE_NAME, STORE_GLOBAL]
HAVE_ARGUMENT = chr(dis.HAVE_ARGUMENT)

# !!! NOTE BEFORE INCLUDING IN PYTHON DISTRIBUTION !!!
# To clear up issues caused by the duplication of data structures between
# the real Python modulefinder and this duplicate version, packagePathMap
# and replacePackageMap are imported from the actual modulefinder. This
# should be changed back to the assigments that are commented out below.
# There are also py2exe specific pieces at the bottom of this file.


# Modulefinder does a good job at simulating Python's, but it can not
# handle __path__ modifications packages make at runtime.  Therefore there
# is a mechanism whereby you can register extra paths in this map for a
# package, and it will be honored.

# Note this is a mapping is lists of paths.
#~ packagePathMap = {}
from modulefinder import packagePathMap

# A Public interface
def AddPackagePath(packagename, path):
    paths = packagePathMap.get(packagename, [])
    paths.append(path)
    packagePathMap[packagename] = paths

#~ replacePackageMap = {}
from modulefinder import replacePackageMap

# This ReplacePackage mechanism allows modulefinder to work around the
# way the _xmlplus package injects itself under the name "xml" into
# sys.modules at runtime by calling ReplacePackage("_xmlplus", "xml")
# before running ModuleFinder.

def ReplacePackage(oldname, newname):
    replacePackageMap[oldname] = newname


class Module:

    def __init__(self, name, file=None, path=None):
        self.__name__ = name
        self.__file__ = file
        self.__path__ = path
        self.__code__ = None
        # The set of global names that are assigned to in the module.
        # This includes those names imported through starimports of
        # Python modules.
        self.globalnames = {}
        # The set of starimports this module did that could not be
        # resolved, ie. a starimport from a non-Python module.
        self.starimports = {}

    def __repr__(self):
        s = "Module(%r" % (self.__name__,)
        if self.__file__ is not None:
            s = s + ", %r" % (self.__file__,)
        if self.__path__ is not None:
            s = s + ", %r" % (self.__path__,)
        s = s + ")"
        return s

class ModuleFinder:

    def __init__(self, path=None, debug=0, excludes=[], replace_paths=[]):
        if path is None:
            path = sys.path
        self.path = path
        self.modules = {}
        self.badmodules = {}
        self.debug = debug
        self.indent = 0
        self.excludes = excludes
        self.replace_paths = replace_paths
        self.processed_paths = []   # Used in debugging only

    def msg(self, level, str, *args):
        if level <= self.debug:
            for i in range(self.indent):
                print "   ",
            print str,
            for arg in args:
                print repr(arg),
            print

    def msgin(self, *args):
        level = args[0]
        if level <= self.debug:
            self.indent = self.indent + 1
            self.msg(*args)

    def msgout(self, *args):
        level = args[0]
        if level <= self.debug:
            self.indent = self.indent - 1
            self.msg(*args)

    def run_script(self, pathname):
        self.msg(2, "run_script", pathname)
        fp = open(pathname, READ_MODE)
        stuff = ("", "r", imp.PY_SOURCE)
        self.load_module('__main__', fp, pathname, stuff)

    def load_file(self, pathname):
        dir, name = os.path.split(pathname)
        name, ext = os.path.splitext(name)
        fp = open(pathname, READ_MODE)
        stuff = (ext, "r", imp.PY_SOURCE)
        self.load_module(name, fp, pathname, stuff)

    def import_hook(self, name, caller=None, fromlist=None, level=-1):
        self.msg(3, "import_hook", name, caller, fromlist, level)
        parent = self.determine_parent(caller, level=level)
        q, tail = self.find_head_package(parent, name)
        m = self.load_tail(q, tail)
        if not fromlist:
            return q
        if m.__path__:
            self.ensure_fromlist(m, fromlist)
        return None

    def determine_parent(self, caller, level=-1):
        self.msgin(4, "determine_parent", caller, level)
        if not caller or level == 0:
            self.msgout(4, "determine_parent -> None")
            return None
        pname = caller.__name__
        if level >= 1: # relative import
            if caller.__path__:
                level -= 1
            if level == 0:
                parent = self.modules[pname]
                assert parent is caller
                self.msgout(4, "determine_parent ->", parent)
                return parent
            if pname.count(".") < level:
                raise ImportError, "relative importpath too deep"
            pname = ".".join(pname.split(".")[:-level])
            parent = self.modules[pname]
            self.msgout(4, "determine_parent ->", parent)
            return parent
        if caller.__path__:
            parent = self.modules[pname]
            assert caller is parent
            self.msgout(4, "determine_parent ->", parent)
            return parent
        if '.' in pname:
            i = pname.rfind('.')
            pname = pname[:i]
            parent = self.modules[pname]
            assert parent.__name__ == pname
            self.msgout(4, "determine_parent ->", parent)
            return parent
        self.msgout(4, "determine_parent -> None")
        return None

    def find_head_package(self, parent, name):
        self.msgin(4, "find_head_package", parent, name)
        if '.' in name:
            i = name.find('.')
            head = name[:i]
            tail = name[i+1:]
        else:
            head = name
            tail = ""
        if parent:
            qname = "%s.%s" % (parent.__name__, head)
        else:
            qname = head
        q = self.import_module(head, qname, parent)
        if q:
            self.msgout(4, "find_head_package ->", (q, tail))
            return q, tail
        if parent:
            qname = head
            parent = None
            q = self.import_module(head, qname, parent)
            if q:
                self.msgout(4, "find_head_package ->", (q, tail))
                return q, tail
        self.msgout(4, "raise ImportError: No module named", qname)
        raise ImportError, "No module named " + qname

    def load_tail(self, q, tail):
        self.msgin(4, "load_tail", q, tail)
        m = q
        while tail:
            i = tail.find('.')
            if i < 0: i = len(tail)
            head, tail = tail[:i], tail[i+1:]
            mname = "%s.%s" % (m.__name__, head)
            m = self.import_module(head, mname, m)
            if not m:
                self.msgout(4, "raise ImportError: No module named", mname)
                raise ImportError, "No module named " + mname
        self.msgout(4, "load_tail ->", m)
        return m

    def ensure_fromlist(self, m, fromlist, recursive=0):
        self.msg(4, "ensure_fromlist", m, fromlist, recursive)
        for sub in fromlist:
            if sub == "*":
                if not recursive:
                    all = self.find_all_submodules(m)
                    if all:
                        self.ensure_fromlist(m, all, 1)
            elif not hasattr(m, sub):
                subname = "%s.%s" % (m.__name__, sub)
                submod = self.import_module(sub, subname, m)
                if not submod:
                    raise ImportError, "No module named " + subname

    def find_all_submodules(self, m):
        if not m.__path__:
            return
        modules = {}
        # 'suffixes' used to be a list hardcoded to [".py", ".pyc", ".pyo"].
        # But we must also collect Python extension modules - although
        # we cannot separate normal dlls from Python extensions.
        suffixes = []
        for triple in imp.get_suffixes():
            suffixes.append(triple[0])
        for dir in m.__path__:
            try:
                names = os.listdir(dir)
            except os.error:
                self.msg(2, "can't list directory", dir)
                continue
            for name in names:
                mod = None
                for suff in suffixes:
                    n = len(suff)
                    if name[-n:] == suff:
                        mod = name[:-n]
                        break
                if mod and mod != "__init__":
                    modules[mod] = mod
        return modules.keys()

    def import_module(self, partname, fqname, parent):
        self.msgin(3, "import_module", partname, fqname, parent)
        try:
            m = self.modules[fqname]
        except KeyError:
            pass
        else:
            self.msgout(3, "import_module ->", m)
            return m
        if self.badmodules.has_key(fqname):
            self.msgout(3, "import_module -> None")
            return None
        if parent and parent.__path__ is None:
            self.msgout(3, "import_module -> None")
            return None
        try:
            fp, pathname, stuff = self.find_module(partname,
                                                   parent and parent.__path__, parent)
        except ImportError:
            self.msgout(3, "import_module ->", None)
            return None
        try:
            m = self.load_module(fqname, fp, pathname, stuff)
        finally:
            if fp: fp.close()
        if parent:
            setattr(parent, partname, m)
        self.msgout(3, "import_module ->", m)
        return m

    def load_module(self, fqname, fp, pathname, (suffix, mode, type)):
        self.msgin(2, "load_module", fqname, fp and "fp", pathname)
        if type == imp.PKG_DIRECTORY:
            m = self.load_package(fqname, pathname)
            self.msgout(2, "load_module ->", m)
            return m
        if type == imp.PY_SOURCE:
            co = compile(fp.read()+'\n', pathname, 'exec')
        elif type == imp.PY_COMPILED:
            if fp.read(4) != imp.get_magic():
                self.msgout(2, "raise ImportError: Bad magic number", pathname)
                raise ImportError, "Bad magic number in %s" % pathname
            fp.read(4)
            co = marshal.load(fp)
        else:
            co = None
        m = self.add_module(fqname)
        m.__file__ = pathname
        if co:
            if self.replace_paths:
                co = self.replace_paths_in_code(co)
            m.__code__ = co
            self.scan_code(co, m)
        self.msgout(2, "load_module ->", m)
        return m

    def _add_badmodule(self, name, caller):
        if name not in self.badmodules:
            self.badmodules[name] = {}
        self.badmodules[name][caller.__name__] = 1

    def _safe_import_hook(self, name, caller, fromlist, level=-1):
        # wrapper for self.import_hook() that won't raise ImportError
        if name in self.badmodules:
            self._add_badmodule(name, caller)
            return
        try:
            self.import_hook(name, caller, level=level)
        except ImportError, msg:
            self.msg(2, "ImportError:", str(msg))
            self._add_badmodule(name, caller)
        else:
            if fromlist:
                for sub in fromlist:
                    if sub in self.badmodules:
                        self._add_badmodule(sub, caller)
                        continue
                    try:
                        self.import_hook(name, caller, [sub], level=level)
                    except ImportError, msg:
                        self.msg(2, "ImportError:", str(msg))
                        fullname = name + "." + sub
                        self._add_badmodule(fullname, caller)

    def scan_opcodes(self, co,
                     unpack = struct.unpack):
        # Scan the code, and yield 'interesting' opcode combinations
        # Version for Python 2.4 and older
        code = co.co_code
        names = co.co_names
        consts = co.co_consts
        while code:
            c = code[0]
            if c in STORE_OPS:
                oparg, = unpack('<H', code[1:3])
                yield "store", (names[oparg],)
                code = code[3:]
                continue
            if c == LOAD_CONST and code[3] == IMPORT_NAME:
                oparg_1, oparg_2 = unpack('<xHxH', code[:6])
                yield "import", (consts[oparg_1], names[oparg_2])
                code = code[6:]
                continue
            if c >= HAVE_ARGUMENT:
                code = code[3:]
            else:
                code = code[1:]

    def scan_opcodes_25(self, co,
                     unpack = struct.unpack):
        # Scan the code, and yield 'interesting' opcode combinations
        # Python 2.5 version (has absolute and relative imports)
        code = co.co_code
        names = co.co_names
        consts = co.co_consts
        LOAD_LOAD_AND_IMPORT = LOAD_CONST + LOAD_CONST + IMPORT_NAME
        while code:
            c = code[0]
            if c in STORE_OPS:
                oparg, = unpack('<H', code[1:3])
                yield "store", (names[oparg],)
                code = code[3:]
                continue
            if code[:9:3] == LOAD_LOAD_AND_IMPORT:
                oparg_1, oparg_2, oparg_3 = unpack('<xHxHxH', code[:9])
                level = consts[oparg_1]
                if level == -1: # normal import
                    yield "import", (consts[oparg_2], names[oparg_3])
                elif level == 0: # absolute import
                    yield "absolute_import", (consts[oparg_2], names[oparg_3])
                else: # relative import
                    yield "relative_import", (level, consts[oparg_2], names[oparg_3])
                code = code[9:]
                continue
            if c >= HAVE_ARGUMENT:
                code = code[3:]
            else:
                code = code[1:]

    def scan_code(self, co, m):
        code = co.co_code
        if sys.version_info >= (2, 5):
            scanner = self.scan_opcodes_25
        else:
            scanner = self.scan_opcodes
        for what, args in scanner(co):
            if what == "store":
                name, = args
                m.globalnames[name] = 1
            elif what in ("import", "absolute_import"):
                fromlist, name = args
                have_star = 0
                if fromlist is not None:
                    if "*" in fromlist:
                        have_star = 1
                    fromlist = [f for f in fromlist if f != "*"]
                if what == "absolute_import": level = 0
                else: level = -1
                self._safe_import_hook(name, m, fromlist, level=level)
                if have_star:
                    # We've encountered an "import *". If it is a Python module,
                    # the code has already been parsed and we can suck out the
                    # global names.
                    mm = None
                    if m.__path__:
                        # At this point we don't know whether 'name' is a
                        # submodule of 'm' or a global module. Let's just try
                        # the full name first.
                        mm = self.modules.get(m.__name__ + "." + name)
                    if mm is None:
                        mm = self.modules.get(name)
                    if mm is not None:
                        m.globalnames.update(mm.globalnames)
                        m.starimports.update(mm.starimports)
                        if mm.__code__ is None:
                            m.starimports[name] = 1
                    else:
                        m.starimports[name] = 1
            elif what == "relative_import":
                level, fromlist, name = args
                if name:
                    self._safe_import_hook(name, m, fromlist, level=level)
                else:
                    parent = self.determine_parent(m, level=level)
                    self._safe_import_hook(parent.__name__, None, fromlist, level=0)
            else:
                # We don't expect anything else from the generator.
                raise RuntimeError(what)

        for c in co.co_consts:
            if isinstance(c, type(co)):
                self.scan_code(c, m)

    def load_package(self, fqname, pathname):
        self.msgin(2, "load_package", fqname, pathname)
        newname = replacePackageMap.get(fqname)
        if newname:
            fqname = newname
        m = self.add_module(fqname)
        m.__file__ = pathname
        m.__path__ = [pathname]

        # As per comment at top of file, simulate runtime __path__ additions.
        m.__path__ = m.__path__ + packagePathMap.get(fqname, [])

        fp, buf, stuff = self.find_module("__init__", m.__path__)
        self.load_module(fqname, fp, buf, stuff)
        self.msgout(2, "load_package ->", m)
        return m

    def add_module(self, fqname):
        if self.modules.has_key(fqname):
            return self.modules[fqname]
        self.modules[fqname] = m = Module(fqname)
        return m

    def find_module(self, name, path, parent=None):
        if parent is not None:
            # assert path is not None
            fullname = parent.__name__+'.'+name
        else:
            fullname = name
        if fullname in self.excludes:
            self.msgout(3, "find_module -> Excluded", fullname)
            raise ImportError, name

        if path is None:
            if name in sys.builtin_module_names:
                return (None, None, ("", "", imp.C_BUILTIN))

            path = self.path
        return imp.find_module(name, path)

    def report(self):
        """Print a report to stdout, listing the found modules with their
        paths, as well as modules that are missing, or seem to be missing.
        """
        print
        print "  %-25s %s" % ("Name", "File")
        print "  %-25s %s" % ("----", "----")
        # Print modules found
        keys = self.modules.keys()
        keys.sort()
        for key in keys:
            m = self.modules[key]
            if m.__path__:
                print "P",
            else:
                print "m",
            print "%-25s" % key, m.__file__ or ""

        # Print missing modules
        missing, maybe = self.any_missing_maybe()
        if missing:
            print
            print "Missing modules:"
            for name in missing:
                mods = self.badmodules[name].keys()
                mods.sort()
                print "?", name, "imported from", ', '.join(mods)
        # Print modules that may be missing, but then again, maybe not...
        if maybe:
            print
            print "Submodules thay appear to be missing, but could also be",
            print "global names in the parent package:"
            for name in maybe:
                mods = self.badmodules[name].keys()
                mods.sort()
                print "?", name, "imported from", ', '.join(mods)

    def any_missing(self):
        """Return a list of modules that appear to be missing. Use
        any_missing_maybe() if you want to know which modules are
        certain to be missing, and which *may* be missing.
        """
        missing, maybe = self.any_missing_maybe()
        return missing + maybe

    def any_missing_maybe(self):
        """Return two lists, one with modules that are certainly missing
        and one with modules that *may* be missing. The latter names could
        either be submodules *or* just global names in the package.

        The reason it can't always be determined is that it's impossible to
        tell which names are imported when "from module import *" is done
        with an extension module, short of actually importing it.
        """
        missing = []
        maybe = []
        for name in self.badmodules:
            if name in self.excludes:
                continue
            i = name.rfind(".")
            if i < 0:
                missing.append(name)
                continue
            subname = name[i+1:]
            pkgname = name[:i]
            pkg = self.modules.get(pkgname)
            if pkg is not None:
                if pkgname in self.badmodules[name]:
                    # The package tried to import this module itself and
                    # failed. It's definitely missing.
                    missing.append(name)
                elif subname in pkg.globalnames:
                    # It's a global in the package: definitely not missing.
                    pass
                elif pkg.starimports:
                    # It could be missing, but the package did an "import *"
                    # from a non-Python module, so we simply can't be sure.
                    maybe.append(name)
                else:
                    # It's not a global in the package, the package didn't
                    # do funny star imports, it's very likely to be missing.
                    # The symbol could be inserted into the package from the
                    # outside, but since that's not good style we simply list
                    # it missing.
                    missing.append(name)
            else:
                missing.append(name)
        missing.sort()
        maybe.sort()
        return missing, maybe

    def replace_paths_in_code(self, co):
        new_filename = original_filename = os.path.normpath(co.co_filename)
        for f, r in self.replace_paths:
            if original_filename.startswith(f):
                new_filename = r + original_filename[len(f):]
                break

        if self.debug and original_filename not in self.processed_paths:
            if new_filename != original_filename:
                self.msgout(2, "co_filename %r changed to %r" \
                                    % (original_filename,new_filename,))
            else:
                self.msgout(2, "co_filename %r remains unchanged" \
                                    % (original_filename,))
            self.processed_paths.append(original_filename)

        consts = list(co.co_consts)
        for i in range(len(consts)):
            if isinstance(consts[i], type(co)):
                consts[i] = self.replace_paths_in_code(consts[i])

        return types.CodeType(co.co_argcount, co.co_nlocals, co.co_stacksize,
                         co.co_flags, co.co_code, tuple(consts), co.co_names,
                         co.co_varnames, new_filename, co.co_name,
                         co.co_firstlineno, co.co_lnotab,
                         co.co_freevars, co.co_cellvars)


def test():
    # Parse command line
    import getopt
    try:
        opts, args = getopt.getopt(sys.argv[1:], "dmp:qx:")
    except getopt.error, msg:
        print msg
        return

    # Process options
    debug = 1
    domods = 0
    addpath = []
    exclude = []
    for o, a in opts:
        if o == '-d':
            debug = debug + 1
        if o == '-m':
            domods = 1
        if o == '-p':
            addpath = addpath + a.split(os.pathsep)
        if o == '-q':
            debug = 0
        if o == '-x':
            exclude.append(a)

    # Provide default arguments
    if not args:
        script = "hello.py"
    else:
        script = args[0]

    # Set the path based on sys.path and the script directory
    path = sys.path[:]
    path[0] = os.path.dirname(script)
    path = addpath + path
    if debug > 1:
        print "path:"
        for item in path:
            print "   ", repr(item)

    # Create the module finder and turn its crank
    mf = ModuleFinder(path, debug, exclude)
    for arg in args[1:]:
        if arg == '-m':
            domods = 1
            continue
        if domods:
            if arg[-2:] == '.*':
                mf.import_hook(arg[:-2], None, ["*"])
            else:
                mf.import_hook(arg)
        else:
            mf.load_file(arg)
    mf.run_script(script)
    mf.report()
    return mf  # for -i debugging


if __name__ == '__main__':
    try:
        mf = test()
    except KeyboardInterrupt:
        print "\n[interrupt]"



# py2exe specific portion - this should be removed before inclusion in the
# Python distribution

import tempfile
import urllib

try:
    set
except NameError:
    from sets import Set as set

Base = ModuleFinder
del ModuleFinder

# Much inspired by Toby Dickenson's code:
# http://www.tarind.com/depgraph.html
class ModuleFinder(Base):
    def __init__(self, *args, **kw):
        self._depgraph = {}
        self._types = {}
        self._last_caller = None
        self._scripts = set()
        Base.__init__(self, *args, **kw)

    def run_script(self, pathname):
        # Scripts always end in the __main__ module, but we possibly
        # have more than one script in py2exe, so we want to keep
        # *all* the pathnames.
        self._scripts.add(pathname)
        Base.run_script(self, pathname)

    def import_hook(self, name, caller=None, fromlist=None, level=-1):
        old_last_caller = self._last_caller
        try:
            self._last_caller = caller
            return Base.import_hook(self,name,caller,fromlist,level)
        finally:
            self._last_caller = old_last_caller

    def import_module(self,partnam,fqname,parent):
        r = Base.import_module(self,partnam,fqname,parent)
        if r is not None and self._last_caller:
            self._depgraph.setdefault(self._last_caller.__name__, set()).add(r.__name__)
        return r

    def load_module(self, fqname, fp, pathname, (suffix, mode, typ)):
        r = Base.load_module(self, fqname, fp, pathname, (suffix, mode, typ))
        if r is not None:
            self._types[r.__name__] = typ
        return r

    def create_xref(self):
        # this code probably needs cleanup
        depgraph = {}
        importedby = {}
        for name, value in self._depgraph.items():
            depgraph[name] = list(value)
            for needs in value:
                importedby.setdefault(needs, set()).add(name)

        names = self._types.keys()
        names.sort()

        fd, htmlfile = tempfile.mkstemp(".html")
        ofi = open(htmlfile, "w")
        os.close(fd)
        print >> ofi, "<html><title>py2exe cross reference for %s</title><body>" % sys.argv[0]

        print >> ofi, "<h1>py2exe cross reference for %s</h1>" % sys.argv[0]

        for name in names:
            if self._types[name] in (imp.PY_SOURCE, imp.PKG_DIRECTORY):
                print >> ofi, '<a name="%s"><b><tt>%s</tt></b></a>' % (name, name)
                if name == "__main__":
                    for fname in self._scripts:
                        path = urllib.pathname2url(os.path.abspath(fname))
                        print >> ofi, '<a target="code" href="%s" type="text/plain"><tt>%s</tt></a> ' \
                              % (path, fname)
                    print >> ofi, '<br>imports:'
                else:
                    fname = urllib.pathname2url(self.modules[name].__file__)
                    print >> ofi, '<a target="code" href="%s" type="text/plain"><tt>%s</tt></a><br>imports:' \
                          % (fname, self.modules[name].__file__)
            else:
                fname = self.modules[name].__file__
                if fname:
                    print >> ofi, '<a name="%s"><b><tt>%s</tt></b></a> <tt>%s</tt><br>imports:' \
                          % (name, name, fname)
                else:
                    print >> ofi, '<a name="%s"><b><tt>%s</tt></b></a> <i>%s</i><br>imports:' \
                          % (name, name, TYPES[self._types[name]])

            if name in depgraph:
                needs = depgraph[name]
                for n in needs:
                    print >>  ofi, '<a href="#%s"><tt>%s</tt></a> ' % (n, n)
            print >> ofi, "<br>\n"

            print >> ofi, 'imported by:'
            if name in importedby:
                for i in importedby[name]:
                    print >> ofi, '<a href="#%s"><tt>%s</tt></a> ' % (i, i)

            print >> ofi, "<br>\n"

            print >> ofi, "<br>\n"

        print >> ofi, "</body></html>"
        ofi.close()
        os.startfile(htmlfile)
        # how long does it take to start the browser?
        import threading
        threading.Timer(5, os.remove, args=[htmlfile])


TYPES = {imp.C_BUILTIN: "(builtin module)",
         imp.C_EXTENSION: "extension module",
         imp.IMP_HOOK: "IMP_HOOK",
         imp.PKG_DIRECTORY: "package directory",
         imp.PY_CODERESOURCE: "PY_CODERESOURCE",
         imp.PY_COMPILED: "compiled python module",
         imp.PY_FROZEN: "frozen module",
         imp.PY_RESOURCE: "PY_RESOURCE",
         imp.PY_SOURCE: "python module",
         imp.SEARCH_ERROR: "SEARCH_ERROR"
         }
