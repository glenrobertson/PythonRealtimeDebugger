import sublime
import sublime_plugin
import os, inspect, imp, bdb
from tempfile import NamedTemporaryFile

"""
get a list of functions in a module at the given filename
"""
def get_functions(filename):
    module_name = os.path.basename(filename).replace('%spy' % os.path.extsep, '')
    
    try:      
        module = imp.load_source(module_name, filename, open(filename))
        fn_members = inspect.getmembers(module, inspect.isroutine)
        return [fn for name, fn in fn_members]
    except SyntaxError:
        print "Couldn't load file %s: syntax error" % filename 
        return []

"""
get the line number of the first line of the first selection area
"""
def get_line_number(view):
    first_region = view.sel()[0]
    first_region_line_begin = view.line(first_region).begin()
    region = sublime.Region(0, view.size())
    line_regions = view.lines(region)
    line_begins = map(lambda region: region.begin(), line_regions)
    return line_begins.index(first_region_line_begin) + 1

"""
get the closest function before the cursor in the active view
"""
def get_active_function(view):
    view_content = view.substr(sublime.Region(0, view.size()))
    
    # need to write the source file to a named temp file
    # this is so we can call imp.load_source which requires a real file
    f = NamedTemporaryFile()
    f.write(view_content)
    f.flush()

    functions = get_functions(f.name)

    line_num = get_line_number(view)
    for f in reversed(functions):
        if line_num >= f.func_code.co_firstlineno:
            return f

"""
given a function and argument values
call the function and keep track of the argument values that change per line
return the changed argument values by line as a dict
"""
def get_args_by_line(fn, *args, **kwargs):
    # set up the debugger and run the function
    # after it is run, pdb will have populated line_locals from each frame
    pdb = FunctionFrameFetcher()
    frame_locals = pdb.get_frame_locals(fn, *args, **kwargs)

    # the frames show the arg values before execution
    # to get the values for a line after execution:
    #   we need to get the value of the args from the following frame
    args_by_line = {}
    for i, (line_number, frame_local) in enumerate(frame_locals[1:], start=1):
        prev_line_number, prev_frame_local = frame_locals[i - 1]

        # difference between this frame dict and previous frame dict
        arg_changes = dict(set(frame_local.items()) - set(prev_frame_local.items()))

        if arg_changes:
            if not prev_line_number in args_by_line:
                args_by_line[prev_line_number] = {}

            modified_line_args = args_by_line[prev_line_number]

            for var_name, var_value in arg_changes.iteritems():
                if var_name not in modified_line_args:
                    modified_line_args[var_name] = []
                modified_line_args[var_name].append(var_value)

    return args_by_line

"""
for a function, generate a string to call it, with args/kwargs
to allow use to fill function arguments with concrete values
e.g. a function with the definition foo(a, b, c=None, d=None)
will return the string "foo(a, b, c=None, d=None)"
"""
def get_function_str(func):
    argspec = inspect.getargspec(func) 
    
    # function has kwargs
    if argspec.defaults:
        args = argspec.args[:-len(argspec.defaults)]
        kwargs = argspec.args[-len(argspec.defaults):]
    else:
        args = argspec.args
        kwargs = None
        args_str = ', '.join(args)

    args_str = ', '.join(args)
    if kwargs:
        kwargs_str = ', '.join(['%s = %s' % (k,v) for k,v in zip(kwargs, argspec.defaults)])
        args_str = ', '.join([args_str, kwargs_str])

    return '%s(%s)' % (func.func_name, args_str)

"""
given a function call string, return a (args, kwargs) tuple
e.g. "foo(1, 2, a=3, b=4)" will return: ( (1, 2), {a: 3, b: 4} )
"""
def get_args_from_string(call_str):
    def get_args_and_kwargs(*args, **kwargs):
        return args, kwargs
    arg_string = call_str[call_str.find('(') : call_str.rfind(')')+1]
    return eval('get_args_and_kwargs%s' % arg_string)

"""
given a dictionary of line_number:arg_dict, and a view:
reset the view and insert the args on their line numbers
"""
def show_args_in_view(args_by_line, view, debug_view):
    window = view.window()

    def format_args(args):
        arg_strs = []
        for arg_name, arg_vals in args.iteritems():
            arg_val_str = ",".join([str(v) for v in arg_vals])
            arg_str = "%s = %s" % (arg_name, arg_val_str)
            arg_strs.append(arg_str)
        return "; ".join(arg_strs)

    lines = sorted(args_by_line.keys())

    edit = debug_view.begin_edit()
    debug_view.erase(edit, sublime.Region(0, debug_view.size()))
    debug_string = ""
    for i, line in enumerate(lines):
        prev_line = 1 if i == 0 else lines[i - 1]
        debug_string += "\n" * (line - prev_line)
        debug_string += format_args(args_by_line[line])
    debug_view.insert(edit, 0, debug_string)
    debug_view.end_edit(edit)

    # refresh the view on the screen
    window.focus_view(debug_view)
    window.focus_view(view)


class FunctionFrameFetcher(bdb.Bdb):

    def dispatch_line(self, frame):
        self.frame_locals.append( (frame.f_lineno, dict(frame.f_locals) ))

    def get_frame_locals(self, fn, *args, **kwargs):
        self.frame_locals = []
        self.runcall(fn, *args, **kwargs)
        return self.frame_locals


class PythonStaticDebugger(sublime_plugin.WindowCommand):

    """
    called after a user has filled arg values for a function
    calls the function, gets the list of modified args by line
    displays the args in an empty view
    """
    def on_done(self, text):
        self.active_args, self.active_kw_args = get_args_from_string(text)
        ViewModified.active_args = self.active_args
        ViewModified.active_kw_args = self.active_kw_args

        # call function with args and get modified argument values by line number
        args_by_line = get_args_by_line(self.active_fn, 
            *self.active_args, **self.active_kw_args)

        # get the view for displaying the debug output
        # get the right-most view, erase its content and replace with the argument values
        debug_view = self.window.views()[len(self.window.views())-1]
        # show the args by line in a view
        show_args_in_view(args_by_line, self.view, debug_view)

    def run(self):
        self.view = self.window.active_view()

        self.active_fn = ViewModified.active_fn = get_active_function(self.view)
        if not self.active_fn:
            return

        fn_str = get_function_str(self.active_fn)

        self.window.show_input_panel('Function: ', 
            fn_str, 
            self.on_done, None, None)


class ViewModified(sublime_plugin.EventListener):

    def on_modified(self, view):
        if not hasattr(self, 'active_args') or not hasattr(self, 'active_kw_args'):
            print 'No parameters set'
            return

        self.active_fn = get_active_function(view)
        
        # call function with args and get modified argument values by line number
        args_by_line = get_args_by_line(self.active_fn, 
            *self.active_args, **self.active_kw_args)

        # get the view for displaying the debug output
        # get the right-most view, erase its content and replace with the argument values
        debug_view = view.window().views()[len(view.window().views())-1]
        # show the args by line in a view
        show_args_in_view(args_by_line, view, debug_view)
        

