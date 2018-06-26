"""
Single cell RNA-Seq tools.

Usage:
  scrtools <command> [<args>...]
  scrtools -h | --help
  scrtools -v | --version

Sub-commands:
  Preprocessing:
    aggregate_matrix        Aggregate cellranger-outputted channel-specific count matrices into a single count matrix. It also enables users to import metadata into the count matrix.
  Analyzing:
    cluster                 Perform first-pass analysis using the count matrix generated from 'aggregate_matrix'. This subcommand could perform low quality cell filtration, batch correction, variable gene selection, dimension reduction, diffusion map calculation, graph-based clustering, tSNE visualization. The final results will be written into h5ad-formatted file, which Seurat could load. 
    subcluster              Perform sub-cluster analyses based on one or several clusters obtained by 'cluster'. 
    de_analysis             Detect markers for each cluster by performing differential expression analysis per cluster (within cluster vs. outside cluster). DE tests include Welch's t-test, Fisher's exact test, Mann-Whitney U test. It can also calculate AUROC values for each gene. 
    annotate_cluster        This subcommand is used to automatically annotate cell types for each cluster based on existing markers. Currently, it only works for human and mouse immune cells.
  Plotting:
    plot                    Make static plots, which includes plotting tSNEs by cluster labels and different groups.
    iplot                   Make interactive plots using plotly. The outputs are HTML pages. You can visualize diffusion maps with this sub-command.
    
Options:
  -h, --help          Show help information.
  -v, --version       Show version.

Description:
  This is a tool for analyzing millions of single cell RNA-Seq data.

"""

import matplotlib
matplotlib.use('Agg')

from docopt import docopt
from docopt import DocoptExit
from . import __version__ as VERSION
from . import commands

def main():
	args = docopt(__doc__, version = VERSION, options_first = True)

	command_name = args['<command>']
	command_args = args['<args>']
	command_args.insert(0, command_name)

	try:
		command_class = getattr(commands, command_name)
	except AttributeError:
		print('Unknown command {cmd}!'.format(cmd = command_name))
		raise DocoptExit()
	
	command = command_class(command_args)
	command.execute()   




if __name__ == '__main__':
	main()
