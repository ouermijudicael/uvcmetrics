#!/usr/bin/env python

# High-level functions to convert data to climatology files.
# These are, in my understanding, files which have a time-average of the original
# variables, with the time often restricted to a month or season.
# This is basically a simplified version of plot_data.py.

# Example of running this from the shell:
# python climatology-old.py --path <location of inputs> --output <where to put outputs> --seasons SON DJF --vars TREFHT

# TO DO >>>> run argument: dict of attribute:value to be written out as file global attributes.

import cdms2, math, os, logging
from metrics.fileio.findfiles import *
from metrics.fileio.filetable import *
from metrics.computation.reductions import *
from metrics.packages.amwg.derivations.oaht import *
from metrics.packages.amwg.derivations.ncl_isms import *
from metrics.packages.amwg.derivations.vertical import *
from metrics.packages.amwg.plot_data import derived_var, plotspec
from cdutil.times import Seasons
from pprint import pprint
from metrics.frontend.options import *
# For psutil, see https://github.com/giampaolo/psutil ...
# import psutil # used only for memory profiling
import cProfile, time, resource
from metrics.common import store_provenance

import logging
logger = logging.getLogger(__name__)


class climatology_variable( reduced_variable ):
    def __init__(self,varname,filetable,seasonname='ANN'):
        self.seasonname = seasonname
        if seasonname=='ANN':
            reduced_variable.__init__( self,
               variableid=varname, filetable=filetable,
               reduction_function=(lambda x,vid=None: reduce_time(x,vid=vid)) )
        else:
            season = cdutil.times.Seasons([seasonname])
            reduced_variable.__init__( self,
               variableid=varname, filetable=filetable,
               reduction_function=(lambda x,vid=None: reduce_time_seasonal(x,season)) )

class climatology_squared_variable( reduced_variable ):
    """represents the climatology of the square of a variable.
    This, together with the variable's climatology, is theoretically sufficient for computing
    its variance; but it would be numerically better to use this as a model for a class
    representing the climatology of (var - climo(var))^2."""
    def __init__(self,varname,filetable,seasonname='ANN'):
        duv = derived_var( varname+'_sq', [varname], func=(lambda x: atimesb(x,x)) )
        self.seasonname = seasonname
        if seasonname=='ANN':
            reduced_variable.__init__(
                self,
                variableid=varname+'_sq', filetable=filetable,
                reduction_function=(lambda x,vid=None: reduce_time(x,vid=vid)),
                duvs={ varname+'_sq':duv }, rvs={} )
        else:
            season = cdutil.times.Seasons([seasonname])
            reduced_variable.__init__(
                self,
                variableid=varname+'_sq', filetable=filetable,
                reduction_function=(lambda x,vid=None: reduce_time_seasonal(x,season)),
                duvs={ varname+'_sq':duv }, rvs={} )

class climatology_variance( reduced_variable ):
    """represents a variance - the climatology of (v-climo(v))^2 where v is a variable.
    Note that we're computing the variance on all data, not a sample - so the implicit
    1/N in the average (not 1/(N-1)) is correct."""
    def __init__(self,varname,filetable,seasonname='ANN',rvs={}):
        duv = derived_var( varname+'_var',
                           [varname,'_'.join([varname,seasonname])], func=varvari )
        self.seasonname = seasonname
        if seasonname=='ANN':
            reduced_variable.__init__(
                self,
                variableid=varname+'_var', filetable=filetable,
                reduction_function=(lambda x,vid=None: reduce_time(x,vid=vid)),
                duvs={ varname+'_var':duv }, rvs=rvs )
        else:
            season = cdutil.times.Seasons([seasonname])
            reduced_variable.__init__(
                self,
                variableid=varname+'_var', filetable=filetable,
                reduction_function=(lambda x,vid=None: reduce_time_seasonal(x,season)),
                duvs={ varname+'_var':duv }, rvs=rvs )

def compute_and_write_climatologies_keepvars( varkeys, reduced_variables, season, case='', variant='', path='' ):
    """Computes climatologies and writes them to a file.
    Inputs: varkeys, names of variables whose climatologies are to be computed
            reduced_variables, dict (key:rv) where key is a variable name and rv an instance
               of the class reduced_variable
            season: the season on which the climatologies will be computed
            variant: a string to be inserted in the filename"""
    # Compute the value of every variable we need.
    varvals = {}
    # First compute all the reduced variables
    # Probably this loop consumes most of the running time.  It's what has to read in all the data.
    for key in varkeys:
        if key in reduced_variables:
            varvals[key] = reduced_variables[key].reduce()

    for key in varkeys:
        if key in reduced_variables:
            var = reduced_variables[key]
            if varvals[key] is not None:
                if 'case' in var._file_attributes.keys():
                    case = var._file_attributes['case']+'_'
                    break

    logger.info("writing climatology file for %s %s %s ",case,variant,season)
    if variant!='':
        variant = variant+'_'
    logger.info('case: %s',case)
    logger.info('variant: %s', variant)
    logger.info('season: %s', season)
    filename = case + variant + season + "_climo.nc"
    # ...actually we want to write this to a full directory structure like
    #    root/institute/model/realm/run_name/season/
    value=0
    cdms2.setNetcdfShuffleFlag(value) ## where value is either 0 or 1
    cdms2.setNetcdfDeflateFlag(value) ## where value is either 0 or 1
    cdms2.setNetcdfDeflateLevelFlag(value) ## where value is a integer between 0 and 9 included

    g = cdms2.open( os.path.join(path,filename), 'w' )    # later, choose a better name and a path!
    store_provenance(g)
    for key in varkeys:
        if key in reduced_variables:
            var = reduced_variables[key]
            if varvals[key] is not None:
                varvals[key].id = var.variableid
                varvals[key].reduced_variable=varvals[key].id
                if hasattr(var,'units'):
                    varvals[key].units = var.units+'*'+var.units
                g.write(varvals[key])
                for attr,val in var._file_attributes.items():
                    if not hasattr( g, attr ):
                        setattr( g, attr, val )
    g.season = season
    g.close()
    return varvals,case

def compute_and_write_climatologies( varkeys, reduced_variables, season, case='', variant='', path='' ):
    """Computes climatologies and writes them to a file.
    Inputs: varkeys, names of variables whose climatologies are to be computed
            reduced_variables, dict (key:rv) where key is a variable name and rv an instance
               of the class reduced_variable
            season: the season on which the climatologies will be computed
            variant: a string to be inserted in the filename"""
    # Compute the value of every variable we need.
    # This function does not return the variable values, or even keep them.

    # First compute all the reduced variables
    # Probably this loop consumes most of the running time.  It's what has to read in all the data.
    firsttime = True
    for key in varkeys:
        if key in reduced_variables:
            time0 = time.time()
            #print "jfp",time.ctime()
            varval = reduced_variables[key].reduce()
            #print "jfp",time.ctime(),"reduced",key,"in time",time.time()-time0
            pmemusg = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss # "maximum resident set size"
            pmemusg = pmemusg / 1024./1024.  # On Linux, should be 1024 for MB
            #print "jfp   peak memory",pmemusg,"MB (GB on Linux)"
            #requires psutil process = psutil.Process(os.getpid())
            #requires psutil mem = process.get_memory_info()[0] / float(2**20)
            #print "jfp   process memory",mem,"MB"
        else:
            continue
        if varval is None:
            continue

        var = reduced_variables[key]
        if firsttime:
            firsttime = False
            if case=='':
                case = getattr( var, 'case', '' )
                if case!='':
                    case = var._file_attributes['case']+'_'
            if case=='':
                case = 'nocase_'
            if variant!='':
                variant = variant+'_'
            filename = case + variant + season + "_climo.nc"
            value=0
            cdms2.setNetcdfShuffleFlag(value) ## where value is either 0 or 1
            cdms2.setNetcdfDeflateFlag(value) ## where value is either 0 or 1
            cdms2.setNetcdfDeflateLevelFlag(value) ## where value is a integer between 0 and 9 included

            g = cdms2.open( os.path.join(path,filename), 'w' )    # later, choose a better name and a path!
            # ...actually we want to write this to a full directory structure like
            #    root/institute/model/realm/run_name/season/

        logger.info("writing %s",key,"in climatology file %s",filename)
        varval.id = var.variableid
        varval.reduced_variable=varval.id
        if hasattr(var,'units'):
            varval.units = var.units+'*'+var.units
        g.write(varval)
        for attr,val in var._file_attributes.items():
            if not hasattr( g, attr ):
                setattr( g, attr, val )
    if firsttime:
        logger.error("No variables found.  Did you specify the right input data?")
    else:
        g.season = season
        g.close()
    return case

def climo_driver(opts):
    """ Test driver for setting up data for plots"""
    # This script should just generate climos 
    opts['output']['plots'] = False
    datafiles1 = dirtree_datafiles(opts, modelid = 0)
    filetable1 = basic_filetable(datafiles1, opts)

    myvars = opts['vars']
    allvars = filetable1.list_variables()
    if myvars == ['ALL']:
        myvars = allvars
    else:
        myvars = list(set(myvars)&set(allvars))
        if len(myvars)<len(opts['vars']):
            logger.warning("Some variables are not available. Computing climatologies for %s",myvars)

    cseasons = opts['times']
    if cseasons == []:
       logger.info('Defaulting to all seasons')
       cseasons = ['ANN','DJF','MAM','JJA','SON',
                   'JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    

    #cseasons = ['ANN', 'DJF', 'JJA' ] 
    #cseasons = ['JAN']
    case = ''

    for season in cseasons:
        logger.info('Processing %s', season)

        reduced_variables1 = { var+'_'+season:climatology_variable(var,filetable1,season)
                               for var in myvars }
        # example:             for var in ['TREFHT','FLNT','SOILC']}
        #reduced_variables = {
        #    'TREFHT_ANN': reduced_variable(
        #        variableid='TREFHT', filetable=filetable1,
        #        reduction_function=(lambda x,vid=None: reduce_time(x,vid=vid)) ),
        #    'TREFHT_DJF': reduced_variable(
        #        variableid='TREFHT', filetable=filetable1,
        #        reduction_function=(lambda x,vid=None: reduce_time_seasonal(x,seasonsDJF,vid=vid)) ),
        #    'TREFHT_MAR': reduced_variable(
        #        variableid='TREFHT', filetable=filetable1,
        #        reduction_function=(lambda x,vid=None:
        #                                reduce_time_seasonal(x,Seasons(['MAR']),vid=vid)) )
        #    }
        # Get the case name, used to compute the output file name.
        varkeys = reduced_variables1.keys()
        #varkeys = varkeys[0:2]  # quick version for testing

        casename = ''
        if opts['model'][0]['name'] != None:
           casename = opts['model'][0]['name']
           logger.info('Using %s', casename,' as dataset name')
        if opts['output']['outputdir'] is not None and opts['output']['outputdir']!='':
            outdir = opts['output']['outputdir']
        else:
            outdir = ''
        outdir = os.path.join(outdir, 'climos')
        logger.info('casename: %s', casename)
        if not os.path.isdir(outdir):
            try:
               os.mkdir(outdir) # processOptions() verifies up to the /climos part, so make /climos now
            except:
               logger.exception('Could not create outputdir - %s', outdir)
               quit()
        #rvs,case = compute_and_write_climatologies_keepvars( varkeys, reduced_variables1, season, casename,
        #                                            path=outdir )
        case = compute_and_write_climatologies( varkeys, reduced_variables1, season, casename,
                                                    path=outdir )

        # Repeat for variance, climatology of (var-climo(var))**2/(N-1)
        # using the (still-in-memory) data in the dict reduced_variables.
#        print "jfp\ndoing var..."
#        reduced_variables3 = { var+'_'+season:
#                                   climatology_variance(var,filetable1,season,rvs=rvs)
#                               for var in filetable1.list_variables() }
#        compute_and_write_climatologies( varkeys, reduced_variables3, season, case, 'var',
#                                         path=outdir )

if __name__ == '__main__':
   o = Options()
   o.processCmdLine()
   o.verifyOptions()
   climo_driver(o)
