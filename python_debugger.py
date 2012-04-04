import sublime
import sublime_plugin
import os, inspect, imp, bdb


class FunctionFrameFetcher(bdb.Bdb):

    def dispatch_line(self, frame):
        self.frame_locals.append( (frame.f_lineno, dict(frame.f_locals) ))

    def get_frame_locals(self, fn, *args, **kwargs):
        self.frame_locals = []
        self.runcall(fn, *args, **kwargs)
        return self.frame_locals


class PythonStaticDebugger(sublime_plugin.WindowCommand):
	
    def on_done(self, text):
        pass

    def get_functions(self, filename):
        module_name = os.path.basename(filename).replace('%spy' % os.path.extsep, '')        
        module = imp.load_source(module_name, filename, open(filename))
        fn_members = inspect.getmembers(module, inspect.isroutine)
        return [fn for name, fn in fn_members]

    # get the line number of the first line of the first selection area
    def get_line_number(self):
        first_region = self.view.sel()[0]
        first_region_line_begin = self.view.line(first_region).begin()
        region = sublime.Region(0, self.view.size())
        line_regions = self.view.lines(region)
        line_begins = map(lambda region: region.begin(), line_regions)
        return line_begins.index(first_region_line_begin) + 1

    # get the closest function before the cursor
    def get_active_function(self):
        filename = self.view.file_name()
        line_num = self.get_line_number()
        functions = self.get_functions(filename)

        for f in reversed(functions):
            if line_num >= f.func_code.co_firstlineno:
                return f

    def get_modified_args_by_line(self, fn, *args, **kwargs):
        # set up the debugger and run the function
        # after it is run, pdb will have populated line_locals from each frame
        pdb = FunctionFrameFetcher()
        frame_locals = pdb.get_frame_locals(fn, *args, **kwargs)

        # the frames show the arg values before execution
        # to get the values for a line after execution:
        #   we need to get the value of the args from the following frame
        modified_args_by_line = {}
        for i, (line_number, frame_local) in enumerate(frame_locals[1:], start=1):
            prev_line_number, prev_frame_local = frame_locals[i - 1]

            # difference between this frame dict and previous frame dict
            arg_changes = dict(set(frame_local.items()) - set(prev_frame_local.items()))

            if arg_changes:
                if not prev_line_number in modified_args_by_line:
                    modified_args_by_line[prev_line_number] = []
                modified_args_by_line[prev_line_number].append(arg_changes)

        return modified_args_by_line

    def run(self):
        self.view = self.window.active_view()

        active_fn = self.get_active_function()
        if not active_fn:
            return
        
        modified_args_by_line = self.get_modified_args_by_line(active_fn, 1)

        debug_view = self.window.views()[len(self.window.views())-1]
        edit = debug_view.begin_edit()
        debug_view.erase(edit, sublime.Region(0, debug_view.size()))

        lines = sorted(modified_args_by_line.keys())

        debug_string = ""
        for i, line in enumerate(lines):
            prev_line = 1 if i == 0 else lines[i - 1]

            debug_string += "\n" * (line - prev_line)
            debug_string += '%s' % modified_args_by_line[line]

        debug_view.insert(edit, 0, debug_string)
        debug_view.end_edit(edit)

        self.window.focus_view(debug_view)
        self.window.focus_view(self.view)

