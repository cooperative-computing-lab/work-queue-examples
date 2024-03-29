#!/usr/bin/python

# Copyright (c) 2019- The University of Notre Dame.
# This software is distributed under the GNU General Public License.
# See the file COPYING for details.

from work_queue import *

import os
import sys
import time
import argparse

category_name = "default"

#---Process options on the command line
def parse_arguments():
	parser = argparse.ArgumentParser(description='wq_bwa [options] <ref> <query>')

	parser.add_argument('ref', type=str, help='BWA reference file: Fasta format')
	parser.add_argument('query', type=str, help='BWA query file (Fastq format required)')
	parser.add_argument('--reads', type=int, help="Sets the number of reads per split to use for running the query (default:{}).".format(10000), default=10000)
	parser.add_argument('--port', type=int, help="Sets the port for work_queue to listen on.", default=WORK_QUEUE_DEFAULT_PORT)
	parser.add_argument('--fa', dest="fast_abort", type=int, help="Sets the work_queue fast abort option with the given multiplier (default: off).")
	parser.add_argument('-N', dest="project", help="Sets the project name to <project> (default: none).")
	parser.add_argument('--stats', type=str, help="Prints WQ statistics to <file> (default:off).")
	parser.add_argument('-d', dest="debug", type=str, help="Sets the debug flag for Work Queue. For all debugging output, try 'all' (default: off).")
	parser.add_argument('--cores', type=int, help="Specify task cores needs.")
	parser.add_argument('--memory', type=str, help="Specify task memory needs in MB.")
	parser.add_argument('--disk', type=str, help="Specify task disk needs in MB.")

	return parser.parse_args()


#------------------------------- FUNCTION DEFINITIONS-------------------------------------
def setup_workqueue (args):
	if (args.debug):
		WorkQueue.cctools_debug_flags_set(args.debug)
		print "{} Work Queue debug flags set: {}.\n".format(time.asctime(), args.debug)

	try:
		wq = WorkQueue(args.port)
	except:
		print "Instantiation of Work Queue failed!"
		sys.exit(1)

	print "{} Work Queue listening on port {}.\n".format(time.asctime(), wq.port)

	if(args.fast_abort) :
		wq.activate_fast_abort_category(category_name, args.fast_abort)
		print "{} Work Queue fast abort set to $multiplier.\n".format(time.asctime())

	if(args.project) :
		wq.specify_name(args.project)
		print "{} Work Queue project name set to {}\n".format(time.asctime(), args.project)

	if(args.stats) :
		wq.specify_log(args.stats)
		print "{} Work Queue stats file set to {}\n".format(time.asctime(), args.stats)

	if(args.reads) :
		num_reads = args.reads

	if(args.cores) :
		wq.specify_category_max_resources(category_name, { 'cores' : args.cores })

	if(args.memory) :
		wq.specify_category_max_resources(category_name, { 'memory' : args.memory })

	if(args.disk) :
		wq.specify_category_max_resources(category_name, { 'disk' : args.disk })

	return wq

# Partition data file
def split_query (query_file, num_reads):
	read_count = 0
	num_outputs = 1
	output = open("{}.{}".format(query_file, num_outputs), "wt")
	line_count = 0

	with open(query_file, "rt") as input:
		for line in input:
			if (line[:1] == '@' and (line_count % 4)==0):
				if read_count == num_reads:
					output.close()
					num_outputs+=1
					read_count = 0
					output = open("{}.{}".format(query_file, num_outputs), "wt")
				else:
					read_count+=1

		output.write(line)
		line_count+=1

	output.close()

	print "{} Number of splits of $query_file is {}.\n".format(time.asctime(), num_outputs)
	return num_outputs

# Create and submit tasks
def partition_tasks (wq, ref_file, query_file, num_reads):
	num_splits = split_query(query_file, num_reads)

	for i in range(1, num_splits+1):
		task_query_file = "{}.{}".format(query_file, i)
		task_outfile    = "{}.sam".format(task_query_file)

		command = "./bwa mem {} {} > {}".format(ref_file, task_query_file, task_outfile)
		t = Task(command)

		t.specify_input_file("bwa") 
		t.specify_input_file(local_name=task_query_file, cache=WORK_QUEUE_NOCACHE) 

		#add the ref file indexes
		for ext in  ['', '.amb', '.ann', '.bwt', '.pac', '.sa']:
			t.specify_input_file(ref_file+ext)

		t.specify_tag(str(i)) 

		t.specify_output_file(local_name=task_outfile, cache=WORK_QUEUE_NOCACHE) 
	
		
		t.specify_category(category_name)
	
		taskid = wq.submit(t)
		print "{} Submitted task (id# {}): {}\n".format(time.asctime(), t.tag, t.command)

	return num_splits


# Wait on tasks
def retrieve_tasks (wq, num_tasks, query_file):
	retrieved_tasks = 0

	task_outfile = open('task_outputs.txt', 'at')
	print "{} Waiting for $num_tasks tasks to complete...\n".format(time.asctime())
	while (retrieved_tasks < num_tasks) :
		t = wq.wait(5)

		if t:
			print "{} Task (id# {}) complete: {} (return code {})\n".format(time.asctime(), t.tag, t.command, t.return_status)
			if(t.return_status != 0) :
				print "{} Task (id# {}) failed\n".format(time.asctime(), t.tag)

			if t.output:	
				task_outfile.write(t.output)
				task_outfile.write("=====================================\n\n")

			os.remove("{}.{}".format(query_file, t.tag))

			retrieved_tasks += 1
			print "{} Retrieved {} tasks.\n".format(time.asctime(), retrieved_tasks)
		else :
			print "{} Retrieved {} tasks.\n".format(time.asctime(), retrieved_tasks)

	task_outfile.close()

def merge_tasks (query_file, num_splits):
	sam_outfile = open("{}.sam".format(query_file), 'wt')
	seq_outfile = open("{}.seq".format(query_file), 'wt')

	for i in range(1, num_splits+1):
		task_outfile = "{}.{}.sam".format(query_file, i)
		with open(task_outfile, 'rt') as output:
			for line in output:
				if (line[:1] == '@') :
					sam_outfile.write(line)
				else:
					seq_outfile.write(line)
		os.remove(task_outfile)

	seq_outfile.close()
	sam_outfile.close()

# Main program
if __name__ == '__main__':
	args = parse_arguments()

	wq = setup_workqueue(args)

	num_tasks = partition_tasks(wq, args.ref, args.query, args.reads)
	retrieve_tasks(wq, num_tasks, args.query)
	merge_tasks(args.query, num_tasks)

	sys.exit(0)
