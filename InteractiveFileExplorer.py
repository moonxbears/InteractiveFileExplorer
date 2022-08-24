import subprocess
import os

import time
import tkinter as tk
import tkinter.ttk as tkk
from tkinter import LEFT, TOP, RIGHT, BOTTOM
from tkinter.scrolledtext import ScrolledText
from tkinter import filedialog as fd

import io, hashlib, queue, sys, time, threading, traceback
import code
from importlib import reload

processes = ""
tests = ""

class Pipe:
	#mock stdin stdout or stderr

	def __init__(self):
		self.buffer = queue.Queue()
		self.reading = False

	def write(self, data):
		self.buffer.put(data)

	def flush(self):
		pass

	def readline(self):
		self.reading = True
		line = self.buffer.get()
		self.reading = False
		return line


class Console(tk.Frame):
	#A tkinter widget which behaves like an interpreter
	
	foreground_error = 'red'

	def __init__(self, parent, _locals, exit_callback):
		super().__init__(parent)

		self.text = ConsoleText(self, wrap=tk.WORD)
		self.text.pack(fill=tk.BOTH, expand=True)

		self.shell = code.InteractiveConsole(_locals)

		# make the enter key call the self.enter function
		self.text.bind("<Return>", self.enter)
		self.prompt_flag = True
		self.command_running = False
		self.exit_callback = exit_callback

		# replace all input and output
		sys.stdout = Pipe()
		sys.stderr = Pipe()
		sys.stdin = Pipe()

		self.readFromPipe(sys.stdout, "stdout")
		self.readFromPipe(sys.stderr, "stderr", foreground=self.foreground_error)

	def prompt(self):
		"""Add a '>>> ' to the console"""
		self.prompt_flag = True

	def readFromPipe(self, pipe: Pipe, tag_name, **kwargs):
		"""Method for writing data from the replaced stdin and stdout to the console widget"""

		# write the >>>
		if self.prompt_flag and not sys.stdin.reading:
			self.text.prompt()
			self.prompt_flag = False

		# get data from buffer
		str_io = io.StringIO()
		while not pipe.buffer.empty():
			c = pipe.buffer.get()
			str_io.write(c)

		# write to console
		str_data = str_io.getvalue()
		if str_data:
			self.text.write(str_data, tag_name, "prompt_end", **kwargs)

		# loop
		self.after(50, lambda: self.readFromPipe(pipe, tag_name, **kwargs))

	def enter(self, e):
		"""The <Return> key press handler"""

		if sys.stdin.reading:
			# if stdin requested, then put data in stdin instead of running a new command
			line = self.text.consume_last_line()
			line = line[1:] + '\n'
			sys.stdin.buffer.put(line)
			return

		# don't run multiple commands simultaneously
		if self.command_running:
			return

		# get the command text
		command = self.text.read_last_line()
		try:
			# compile it
			compiled = code.compile_command(command)
			is_complete_command = compiled is not None
		except (SyntaxError, OverflowError, ValueError):
			# if there is an error compiling the command, print it to the console
			self.text.consume_last_line()
			self.prompt()
			traceback.print_exc(0)
			return

		# if it is a complete command
		if is_complete_command:
			# consume the line and run the command
			self.text.consume_last_line()
			self.prompt()

			self.command_running = True

			def run_command():
				try:
					self.shell.runcode(compiled)
				except SystemExit:
					self.after(0, self.exit_callback)

				self.command_running = False

			threading.Thread(target=run_command).start()


class ConsoleText(ScrolledText):
	"""
	A Text widget which handles some application logic,
	e.g. having a line of input at the end with everything else being uneditable
	"""
	foreground_input = 'blue'

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		# make edits that occur during on_text_change not cause it to trigger again
		def on_modified(event):
			flag = self.edit_modified()
			if flag:
				self.after(10, self.on_text_change(event))
			self.edit_modified(False)

		self.bind("<<Modified>>", on_modified)

		# store info about what parts of the text have what colour
		# used when colour info is lost and needs to be re-applied
		self.console_tags = []

		# the position just before the prompt (>>>)
		# used when inserting command output and errors
		self.mark_set("prompt_end", 1.0)

		# keep track of where user input/commands start and the committed text ends
		self.committed_hash = None
		self.committed_text_backup = ""
		self.commit_all()

	def prompt(self):
		"""Insert a prompt"""
		self.mark_set("prompt_end", 'end-1c')
		self.mark_gravity("prompt_end", tk.LEFT)
		self.write(">>> ", "prompt", foreground=self.foreground_input)
		self.mark_gravity("prompt_end", tk.RIGHT)

	def commit_all(self):
		"""Mark all text as committed"""
		self.commit_to('end-1c')

	def commit_to(self, pos):
		"""Mark all text up to a certain position as committed"""
		if self.index(pos) in (self.index("end-1c"), self.index("end")):
			# don't let text become un-committed
			self.mark_set("committed_text", "end-1c")
			self.mark_gravity("committed_text", tk.LEFT)
		else:
			# if text is added before the last prompt (">>> "), update the stored position of the tag
			for i, (tag_name, _, _) in reversed(list(enumerate(self.console_tags))):
				if tag_name == "prompt":
					tag_ranges = self.tag_ranges("prompt")
					self.console_tags[i] = ("prompt", tag_ranges[-2], tag_ranges[-1])
					break

		# update the hash and backup
		self.committed_hash = self.get_committed_text_hash()
		self.committed_text_backup = self.get_committed_text()

	def get_committed_text_hash(self):
		"""Get the hash of the committed area - used for detecting an attempt to edit it"""
		return hashlib.md5(self.get_committed_text().encode()).digest()

	def get_committed_text(self):
		"""Get all text marked as committed"""
		return self.get(1.0, "committed_text")

	def write(self, string, tag_name, pos='end-1c', **kwargs):
		"""Write some text to the console"""

		# get position of the start of the text being added
		start = self.index(pos)

		# insert the text
		self.insert(pos, string)
		self.see(tk.END)

		# commit text
		self.commit_to(pos)

		# color text
		self.tag_add(tag_name, start, pos)
		self.tag_config(tag_name, **kwargs)

		# save color in case it needs to be re-colured
		self.console_tags.append((tag_name, start, self.index(pos)))

	def on_text_change(self, event):
		"""If the text is changed, check if the change is part of the committed text, and if it is revert the change"""
		if self.get_committed_text_hash() != self.committed_hash:
			# revert change
			self.mark_gravity("committed_text", tk.RIGHT)
			self.replace(1.0, "committed_text", self.committed_text_backup)
			self.mark_gravity("committed_text", tk.LEFT)

			# re-apply colours
			for tag_name, start, end in self.console_tags:
				self.tag_add(tag_name, start, end)

	def read_last_line(self):
		"""Read the user input, i.e. everything written after the committed text"""
		return self.get("committed_text", "end-1c")

	def consume_last_line(self):
		"""Read the user input as in read_last_line, and mark it is committed"""
		line = self.read_last_line()
		self.commit_all()
		return line

class InteractiveTabs(tk.Frame):
	def __init__(self):
		pass

class InteractiveFileBrowser(tk.Frame):
	
	DefaultDir = os.path.abspath("/")
	DIRPATH = os.getcwd()
	DIROBJECTS = []

	def __init__(self, root):

			self.frame_navigator = tkk.Frame(root)
			self.frame_navigator.place()
			self.label_file_explorer = tkk.Label(root, 
												text = "Interactive Explorer w/ Tkinter",
												width = 100, 
												foreground= "blue")

			self.label_file_explorer.pack(fill=tk.BOTH, expand=False, ipadx=5, ipady=5, padx=5, pady=5, side=TOP)
			self.frame_navigator.pack(fill=tk.BOTH, expand=True, ipadx=5, ipady=5, padx=5, pady=5, side=LEFT)
			self.backb = tkk.Button(self.frame_navigator, text="...", command=self.back)
			self.backb.pack(fill = tk.X, expand=False, side=TOP)
 
			self.trees = tkk.Treeview(self.frame_navigator)
			self.trees.bind("<Double-Button-1>", self.dclick_handler)
			self.trees.bind("<<TreeviewSelect>>", self.select_item)

			self.trees['columns'] = ('Name', 'Path')
			self.trees.column('#0', width=0, stretch=tk.NO)
			self.trees.column('Name', anchor=tk.W, width=160)
			self.trees.column('Path', anchor=tk.W, width=200)
			self.trees.heading('#0', text='', anchor=tk.CENTER)
			self.trees.heading('Name', text = 'name', anchor=tk.W)
			self.trees.heading('Path', text = 'path', anchor=tk.W)

			self.trees_scrollbar = tkk.Scrollbar(self.frame_navigator,  orient="vertical")
			self.trees_scrollbar.pack(fill=tk.Y,  side=tk.RIGHT)
			self.trees_scrollbar.config(command = self.trees.yview)
			self.trees.config(yscrollcommand=self.trees_scrollbar.set)

			self.DIRPATH = self.DefaultDir
			self.loadDIR()

	def select_item(self, event):
		pass

	def dclick_handler(self, event):
		selected = self.trees.focus()
		temp = self.trees.item(selected, 'values')
		try:
			os.chdir(temp[1])
			self.loadDIR()
		except:
			pass

	def back(self):
		os.chdir(os.path.pardir)
		self.loadDIR()

	def setDirectory(self):
		os.chdir(os.getcwd())
		print(os.getcwd())
		self.loadDIR()


	def loadDIR(self):
		
		if os.path.exists(self.DIRPATH):
			self.DIROBJECTS.clear()
			for i in self.trees.get_children():
				self.trees.delete(i)
			self.DIRPATH = os.getcwd()
			#ps_interactive.change_dir()
			try:
				self.DIR = os.scandir(self.DIRPATH)
	
				for index, entry in enumerate(self.DIR):
					self.trees.insert(parent='', index=index, iid=str(index), text='', values=(entry.name, entry.path))

				self.trees.pack(fill = tk.BOTH, expand=True, side=tk.TOP)
			except:
				pass

if __name__ == '__main__':
	root = tk.Tk()
	root.title('Interactive Explorer')
	root.geometry("1000x600")
	root.config(background="black")

	#ps_interactive = Terminal(root)
	dbug = tkk.Frame(root, height = 400)
	IFB = InteractiveFileBrowser(root)
	tabWinControl = tkk.Notebook(root)
	py_interactive = Console(tabWinControl, locals(), root.destroy)
	
	#ps_interactive.dark_mode()

	tabWinControl.pack(fill=tk.Y, expand=True, padx=5, pady=5, side=TOP)

	tabWinControl.add(py_interactive, text="PY")
	
	#tabWinControl.add(ps_interactive, text="PS")

	dbug.pack(fill=tk.BOTH, expand=True, padx=5, pady=5, side=TOP)
	py_interactive.pack(fill=tk.BOTH, expand=True, padx=5, pady=5, side=TOP)
	#ps_interactive.pack(fill=tk.BOTH, expand=True, padx=5, pady=5, side=TOP)

	root.resizable(True, True)
	root.minsize(640, 360)
	root.mainloop()