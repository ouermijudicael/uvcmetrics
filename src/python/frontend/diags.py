#!/usr/bin/env python
# Script for running diagnostics.
# Command-line usage example:
# diags --model path=path,climos=yes --obs path=path,climos=yes,filter='f_startswith("NCEP")' --vars FLUT T --seasons DJF --region Global --package AMWG --output path

### TODO
### Clean up filename generation to make it easier to detect already-generated files
###     (Idealy, just specify the exact, complete filename)
### Look for speed improvements

import hashlib, os, pickle, sys, os, time, re, pdb, logging
from metrics import *
from metrics.fileio.filetable import *
from metrics.fileio.findfiles import *
from metrics.computation.reductions import *
from metrics.frontend.form_filenames import *
from metrics.frontend.amwg_plotting import *
# These next 5 lines really shouldn't be necessary. We should have a top level
# file in packages/ that import them all. Otherwise, this needs done in every
# script that does anything with diags, and would need updated if new packages
# are added, etc.
from metrics.packages.amwg import *
from metrics.packages.lmwg import *
from metrics.packages.diagnostic_groups import *
from metrics.frontend.uvcdat import *
from metrics.frontend.options import *
from pprint import pprint
from metrics.common.utilities import *
import metrics.frontend.defines as defines
import cProfile
from metrics.frontend.it import *
from metrics.computation.region import *
#import debug
import logging
logger = logging.getLogger(__name__)

global vcs_elements   # used to prevent uncontrolled growth of vcs.elements if there are multiple calls of run_diags()
vcs_elements = None

def setManualColormap(canvas=None, level=0):
    """
    This function manualy creates a colormap based on the number of levels
    given. At the moment only levels 14 and 16 are available. The max value
    for the number of levels is 240.
    """
    if (canvas is None) or ((level==0) or (level>17)):
        logger.error("Incorrect use of function. One must suply a vcs canvas and the number of levels must be different than 0.")

    dataColor = {
        0:(100,7.84,57.65),
        1:(100,71.4,75.7),
        2:(70,13.3,13.3),
        3:(100,27.06,0),
        4:(100,64.70,0),
        5:(100,100,0),
        6:(87.06,72.16,52.94),
        7:(96.08,90.20,74.51),
        8:(58.82,74.51,19.61),
        9:(74.51,94.12,39.22),
        10:(12.55,69.80,66.7),
        11:(69.02,87.84,90.2),
        12:(47.06,60.78,94.9),
        13:(23.53,39.22,90.2),
        14:(0,0,78.43),
        15:(57.65,43.92,85.88),
        16:(84.31,52.55,93.33)}

    step = 256.0/float(level);
    currStep = 255;  # max number of levels in a colormap

    for i in range(17):  # 17 entries in dataColor
        for j in range(int(step)):
            #if round(currStep)-j <= 0:
            if currStep-j < 0:
                return
            canvas.setcolorcell(currStep-j, dataColor[i][0], dataColor[i][1], dataColor[i][2])
            #canvas.setcolorcell(round(currStep)-j, dataColor[i][0], dataColor[i][1], dataColor[i][2])
        #currStep = float(currStep) - step
        currStep -= int(step)
    canvas.setcolorcell(1, 0.0, 0.0, 0.0)
    canvas.setcolorcell(0, 100.0, 100.0, 100.0)

def getNames(opts, model, obs):
    """ The purpose of this kludge is to get the names of the model and obs 
    specified on the command line for output on the graphic.
    Each of model and obs should be a list of instances of basic_filetable.
    This function returns a dictionary of the form {'model':modelname, 'obs':obsname}
    where modelname and obsname are strings."""
    def get_model_case(filetable):
        files = filetable._filelist
        try:
            f = cdms2.open(files[0])
            case = f.case
            f.close()
        except:
            case = 'not available'
        return case
    from metrics.packages.plotplan import plot_plan
    ft1, ft2 = plot_plan().getfts(model, obs)
    nicknames = {}
    if len(opts['model'])>0 and opts['model'][0]['name'] is not None:
        nicknames['model'] = opts['model'][0]['name']
    else:
        nicknames['model'] = get_model_case(ft1)

    if len(opts['obs'])>0 and opts['obs'][0]['name'] is not None:
        nicknames['obs'] = opts['obs'][0]['name']
    elif ft2 is not None:
        #nicknames['obs'] = ft2.source().split('_')[-1]
        nicknames['obs'] = ft2.source().split('_')[0]
    return nicknames

def setnum( setname ):
    """extracts the plot set number from the full plot set name, and returns the number.
    The plot set name should begin with the set number, e.g.
       setname = ' 2- Line Plots of Annual Implied Northward Transport'"""

    #this assumes a dash is in the string as above; the rest of this function is tooooooo complicated
    if '-' in setname:
        setname = setname.split('-')[0]
        return setname.strip()

    mo = re.search( r'\d', setname )   # matches decimal digits
    if mo is None:
        return None
    index1 = mo.start()                        # index of first match
    mo = re.search( r'\D', setname[index1:] )  # matches anything but decimal digits
    if mo is None:                             # everything past the first digit is another digit
        setnumber = setname[index1:]
    else:
        index2 = mo.start()                    # index of first match
        setnumber = setname[index1:index1+index2]
    return setnumber

def save_regrid( opts ):
    """Saves regrid method as an Options class variable."""
    if 'regrid' not in opts._opts.keys():
        opts['regrid'] = 'esmf-linear'
    if opts['regrid'] == 'esmf-linear' or opts['regrid']=='esmf':
        Options.regridTool = 'esmf'
        Options.regridMethod = 'linear'
    elif opts['regrid'] == 'regrid2':
        Options.regridTool = 'regrid2'
        Options.regridMethod = None
    elif opts['regrid'] == 'esmf-conserv':
        Options.regridTool = 'esmf'
        Options.regridMethod = 'conservative'
    elif opts['regrid'] == 'libcf-linear' or opts['regrid']=='libcf':
        Options.regridTool = 'libcf'
        Options.regridMethod = 'linear'
    else:
        logger.error("do not recognize regrid option %s",opts['regrid'])

def run_diags( opts ):
    global vcs_elements
    # Setup filetable arrays
    modelfts = []
    obsfts = []
    names = {'model':'', 'obs':''}
    for i in range(len(opts['model'])):
        ft = path2filetable(opts, modelid=i)
        if ft._id.nickname=='':
            newid = ft.IDtuple( classid=ft._id.classid, ftno=ft._id.ftno, ftid=ft._id.ftid,
                                nickname='model' )
            ft._id = newid
        modelfts.append(ft)
    for i in range(len(opts['obs'])):
        ft = path2filetable(opts, obsid=i)
        if ft._id.nickname=='':
            newid = ft.IDtuple( classid=ft._id.classid, ftno=ft._id.ftno, ftid=ft._id.ftid,
                                nickname='obs' )
            ft._id = newid
        obsfts.append(ft)

    for i in range(len(modelfts)):
        logging.info('model %s id: %s', i, modelfts[i]._strid)
    for i in range(len(obsfts)):
        logging.info('obs %s id: %s', i, obsfts[i]._strid)

    # Runtime import of user-defined diagnostics
    for module in opts['uservars']:
        amwg_plot_plan.get_user_vars(module)

    # Setup some output things
    outdir = opts['output']['outputdir']
    if outdir is None:
        outdir = os.path.join(os.environ['HOME'],"tmp","diagout")
        logger.warning('Writing output to %s. Override with --outputdir option', outdir)
    # Partsa of the eventual output filenames
    basename = opts['output']['prefix']
    postname = opts['output']['postfix']

    # This should probably be done in verify options()
    if opts['package'] is None:
        logger.critical('Please specify a package name')
        quit()
    else:
        package = opts['package']

    # Check for user-supplied times (eg seasons, months, or annual)
    times = opts.get ('times', None)
    if times is None or times == []:
        times = ['ANN']
        logger.warning("Defaulting to time ANN. You can specify times with --seasons/--seasonally, --months/--monthly or --yearly")
    else:
        logger.info("Using times= %s" ,times)

    # See if any variable options were passed in
    if opts['varopts'] is None:
        opts['varopts'] = [None]

    # See if regions were passed in
    regl = []
    regions = []
    if opts['regions'] == []:
        rname = 'Global'
        regl = [defines.all_regions['Global']]
        regions = [ rectregion(rname, regl) ]
    else:
        rnames = opts['regions']
        for r in rnames:
            regl.append(defines.all_regions[r])
            regions.append(rectregion(r, defines.all_regions[r]))
    logger.info('Using regions %s',regions)

    save_regrid(opts)

    number_diagnostic_plots = 0

    dm = diagnostics_menu()                 # dm = diagnostics menu (package), a dict

    # set up some VCS things if we are going to eventually plot things
    if opts['output']['plots'] == True:
        vcanvas = vcs.init()
        if opts['output']['antialiasing'] is False:
            vcanvas.setantialiasing(0)
        vcsx = vcanvas
        vcanvas.setcolormap('bl_to_darkred') #Set the colormap to the NCAR colors
        vcanvas2 = vcs.init(bg=True, geometry=(1212,1628))
        if opts['output']['antialiasing'] is False:
            vcanvas.setantialiasing(0)
            vcanvas2.setantialiasing(0)
        vcanvas2.portrait()
        vcanvas2.setcolormap('bl_to_darkred') #Set the colormap to the NCAR colors
        if 'LINE-DIAGS' in vcs.listelements('line'):
            LINE = vcanvas.getline('LINE-DIAGS')
        else:
            LINE = vcanvas.createline('LINE-DIAGS', 'default')
            LINE.width = 3.0
            LINE.type = 'solid'
            LINE.color = 242
        if opts['output']['logo'] == False:
            vcanvas.drawlogooff()
            vcanvas2.drawlogooff()
    else:
        # No plots. JSON? XML? NetCDF? etc
        # do something else
        logger.warning('Not plotting. Do we need any setup to produce output files?')

    # Initialize our diagnostics package class
    pclass = dm[package.upper()]()
    # Find which plotsets the user requested which this package offers:
    sm = pclass.list_diagnostic_sets()  # sm = plot set menu, a dict
    if opts['sets'] is None:
        keys = sm.keys()
        keys.sort()
        plotsets = [ keys[1] ]
        logger.warning("plot sets not specified, defaulting to %s",plotsets[0])
    else:
        sndic = { setnum(s):s for s in sm.keys() }   # plot set number:name
        plotsets = []
        for ID in opts['sets']:
            plotsets += [sndic[ID]]

    # Ok, start the main loops.
    for sname in plotsets:
        logger.info("Working on %s plots ",  sname)

        snum = setnum(sname)

        # instantiate the class
        sclass = sm[sname]

        # AMWG set 1 (the tables) is a special case
        #this is another total hack to speedup amwg1.
        if (sclass.number == '1' and package.upper() == 'AMWG'):
            # make tables
            varid = None
            #somehow the default is ['ALL']
            if opts['vars'] != ['ALL']:
                varid = opts['vars'][0]
            filter = opts._opts['obs'][0]['filter']
            obsfilter = None
            if filter != None:
                obsfilter  = filter.split('"')[1]

            computeall = not opts['output']['table']
            table = sclass( modelfts, obsfts, varid=varid, obsfilter=obsfilter, dryrun=opts['dryrun'], sbatch=opts["sbatch"], computeall=computeall, outdir=outdir)
            
            if opts['dryrun'] or varid is not None:
                continue
            #read the files and print the table
            directory = outdir
            if opts['output']['table']:
                directory += '/amwg1_output/'
                table.get_data(directory)
#jfp was            table.write_plot_data(where=directory, fname=directory+'table_output') 
            table.write_plot_data(where=directory, fname=form_filename( form_file_rootname(
                        sclass.number, [], 'table'), 'text' ) )
            continue

        # see if the user specified seasons are valid for this diagnostic
        use_times = list( set(times) & set(pclass.list_seasons()) )

        # Get this list of variables for this set (given these obs/model inputs)
        logging.info('opts vars: %s', opts.get('vars',[]))
        variables = pclass.list_variables( modelfts, obsfts, sname )  # includes many derived variables
        logger.info('var list from pclass: %s', variables)

        # Get the reduced list of variables possibly specified by the user
        if opts.get('vars',['ALL'])!=['ALL']:
            # If the user sepcified variables, use them instead of the complete list
            variables = list( set(variables) & set(opts.get('vars',[])) )
            if len(variables)==0 and len(opts.get('vars',[]))>0:
                logger.warning('Could not find any of the requested variables %s among %s', opts['vars'],
                                pclass.list_variables(modelfts,obsfts,sname) )
                logger.warning("among %s", variables)
                return {}

        # Ok, start the next layer of work - seasons and regions
        # loop over the seasons for this plot
        for utime in use_times:
            for region in regions:
                # Get the current region's name, using the class wizardry.
                region_rect = defines.all_regions[str(region)]
                r_fname = region_rect.filekey
                rname = str(region)
                logger.info('Region: %s', rname)
                logger.info('Region filename: %s', r_fname)

                # loop over variables now
                vcount = len(variables)
                counter = 0
                for ivarid, varid in enumerate(variables):
                    logger.info("Processing variable %s in season %s in plotset %s -variable %s of %s", varid, utime, sname, counter, vcount)
                    counter = counter+1
                    vard = pclass.all_variables( modelfts, obsfts, sname )
                    plotvar = vard[varid]
                    # Find variable options.  If none were requested, that means "all".
                    vvaropts = plotvar.varoptions()
                    if vvaropts is None:
                        if len(opts['varopts'])>0:
                            if opts['varopts']!=[None]:
                                logger.warning("No variable options are available, but these were requested: %s. Continuing as though no variable options were requested.", opts['varopts'])
                        vvaropts = {None:None}
                        varopts = [None]
                    else:
                        if len(opts['varopts'])==0:
                            varopts = vvaropts.keys()
                        else:
                            if opts['varopts']==[] or opts['varopts']==[None]:
                                opts['varopts'] = [ None, 'default', ' default' ]
                            varopts = list( set(vvaropts.keys()) & set(opts['varopts']) )
                            if varopts==[]:
                                logger.warning("Requested varopts incompatible with available varopts, requeseted varopts=%s",opts['varopts'])
                                logger.warning("available varopts for variable %s are %s",varid,vvaropts.keys())
                                logger.warning("No plots will be made.")
                    #get the names of the model and obs passed in from the command line
                    #or a default from the filetable
                    
                    names = getNames(opts, modelfts, obsfts)
                    # now, the most inner loop. Looping over sets then seasons then vars then varopts
                    for aux in varopts:
                        #plot = sclass( modelfts, obsfts, varid, utime, region, vvaropts[aux] )

                        # Since Options is a 2nd class (at best) citizen, we have to do something icky like this.
                        # hoping to change that in a future release. Also, I can see this being useful for amwg set 1.
                        # (Basically, if we output pre-defined json for the tables they can be trivially sorted)
                        if '5' in snum and package.upper() == 'LMWG' and opts['output']['json'] == True:
                            plot = sclass( modelfts, obsfts, varid, utime, region, vvaropts[aux], jsonflag=True )
                        else:
                            if snum == '14' and package.upper() == 'AMWG': #Taylor diagrams
                                #this is a total kludge so that the list of variables is passed in for processing
                                plot = sclass( modelfts, obsfts, variables, utime, region, vvaropts[aux],
                                               plotparms = { 'model':{}, 'obs':{}, 'diff':{} } )
                            else:
                                plot = sclass(
                                    modelfts, obsfts, varid, utime, region, vvaropts[aux], names=names,
                                    plotparms = { 'model':{'levels':opts['levels'], 'colormap':opts['colormaps']['model']},
                                                  'obs':{'levels':opts['levels'], 'colormap':opts['colormaps']['obs']},
                                                  'diff':{'levels':opts['difflevels'], 'colormap':opts['colormaps']['diff']} } )
                        if vcs_elements is None:
                            vcs_elements = dictcopy3(vcs.elements)
                        else:
                            vcsdisplays = vcs.elements['display']
                            vcs.elements = dictcopy3(vcs_elements)
                            vcs.elements['display'] = vcsdisplays

                        # Do the work (reducing variables, etc)
                        res = plot.compute(newgrid=0) # newgrid=0 for original grid, -1 for coarse
                        # typically res is a list of uvc_simple_plotspec.  But an item might be a tuple.

                        if res is not None and len(res)>0 and type(res) is not str: # Success, we have some plots to plot
                            logger.info('--------------------------------- res is not none')

                            frname = form_file_rootname(
                                snum, [varid], 'variable', dir=outdir, season=utime, # basen=basename,
                                postn=postname, region=r_fname, aux=[aux] )
                            
                            if opts['output']['plots'] == True:
                                displayunits = opts.get('displayunits', None)
                                makeplots(res, vcanvas, vcanvas2, varid, frname, plot, package, opts, displayunits=displayunits)
                                number_diagnostic_plots += 1

                                #tracker.print_diff()


                            if opts['output']['xml'] == True:
                                # Also, write the nc output files and xml.
                                # Probably make this a command line option.
                                if res.__class__.__name__ is 'uvc_composite_plotspec':
                                    resc = res
                                else:
                                    #new kludge
                                    for PLOT in res:
                                        if hasattr(PLOT, 'title'):
                                            PLOT.title = PLOT.title.replace('\n', ' ')
                                            if varid not in PLOT.title and utime not in PLOT.title:
                                                PLOT.title = varid + ' ' + utime + ' ' + PLOT.title
                                    resc = uvc_composite_plotspec( res )
                                filenames = resc.write_plot_data("xml-NetCDF", frname )
                                logger.info("wrote plots %s to %s",resc.title, filenames)

                        elif res is not None:
                            ############################################################
                            # it appears that the rest of this code is unnecessary.
                            # the hack to speedup amwg1 does not need this. It's simpler.
                            if type(res) is str:
                                f = open( form_filename( form_file_rootname(
                                            'resstring', [varid], 'table', dir=outdir, season=utime,
                                                         basen=basename, postn=postname, region=r_fname, aux=aux ),
                                                         'text' ))
                                f.write(res)
                                f.close()
                            else:
                                # but len(res)==0, probably plot tables
                                # eventually, education could get rid of the second clause here but I suspect not anytime soon.
                                if opts['output']['table'] == True or res.__class__.__name__ is 'amwg_plot_set1':
                                    resc = res
                                    if basename == '' and postname == '':
                                        where = outdir
                                        fname = ""
                                    else:
                                        where = ""
                                        fname = form_filename( form_file_rootname(
                                            'res0', [], 'table', dir=outdir, season=utime,
                                            basen=basename, region=r_fname ), 'text' )

                                    filenames = resc.write_plot_data("text", where=where, fname=fname)
                                    number_diagnostic_plots += 1
                                    logger.info( "-------> wrote table %s to %s", resc.title, filenames)
                                else:
                                    logger.info('No data to plot for %s %s', varid, aux)

    vcanvas.close()
    vcanvas2.close()
#    vcanvas.destroy()
#    vcanvas2.destroy()
    logger.info("total number of (compound) diagnostic plots generated = %s", number_diagnostic_plots)

    # If this were called from multidiags, the names dictionary would be helpful.  In particular,
    # it will help to not have to re-open a file to re-compute the case name for the model.
    return names


def makeplots(res, vcanvas, vcanvas2, varid, frname, plot, package, opts, displayunits=None):
    # need to add plot and pacakge for the amwg 11,12 special cases. need to rethink how to deal with that
    # At this loop level we are making one compound plot.  In consists
    # of "single plots", each of which we would normally call "one" plot.
    # But some "single plots" are made by drawing multiple "simple plots",
    # One on top of the other.  VCS draws one simple plot at a time.
    # Here we'll count up the plots and run through them to build lists
    # of graphics methods and overlay statuses.
    # We are given the list of results from plot(), the 2 VCS canvases and a filename minus the last bit
    cdms2.setAutoBounds(True)   # makes the VCS-computed means the same as when we
    #                             compute means after calling genGenericBounds().
    frnamebase = frname
    nsingleplots = len(res)
    nsimpleplots = nsingleplots + sum([len(resr)-1 for resr in res if type(resr) is tuple])
    gms = nsimpleplots * [None]
    ovly = nsimpleplots * [0]
    onPage = nsingleplots
    ir = 0
    t1 = 0   # timing
    tp = 0   # timing
    tt0 = time.time() # timing
    for r,resr in enumerate(res):
        if type(resr) is tuple:
            for jr,rsr in enumerate(resr):
                gms[ir] = resr[jr].ptype.lower()
                ovly[ir] = jr
                ir += 1
        elif resr is not None:
            gms[ir] = resr.ptype.lower()
            ovly[ir] = 0
            ir += 1
    if None in gms:
        logger.warning("Missing a graphics method. gms=%s",gms)
    # Now get the templates which correspond to the graphics methods and overlay statuses.
    # tmobs[ir] is the template for plotting a simple plot on a page
    #   which has just one single-plot - that's vcanvas
    # tmmobs[ir] is the template for plotting a simple plot on a page
    #   which has the entire compound plot - that's vcanvas2
    gmobs, tmobs, tmmobs = return_templates_graphic_methods( vcanvas, gms, ovly, onPage )
    logger.debug("tmpl nsingleplots= %s nsimpleplots= %s ",nsingleplots , nsimpleplots)
    logger.debug("tmpl gms= %s" , gms)
    logger.debug("tmpl len(res)= %s ovly= %s onPage=%s", len(res),  ovly, onPage)
    logger.debug("tmpl gmobs= %s", gmobs)
    logger.debug('TMOBS/TMMOBS:')
    logger.debug("%s ", tmobs)
    logger.debug("%s ", tmmobs)

    # gmobs provides the correct graphics methods to go with the templates.
    # Unfortunately, for the moment we have to use rmr.presentation instead
    # (below) because it contains some information such as axis and vector
    # scaling which is not yet done as part of
    # So, we are deleting gmobs:
    gmobs = None
    ovly  = None

    vcanvas2.clear()
    source_descr2 = []  # list of strings describing source data in vcanvas2 (composite plot)
    plotcv2 = False
    ir = -1
    # Fixes scale in multiple polar plots on a page
    adjustedScaleCircularPlot = True
    for r,resr in enumerate(res):
        if resr is None:
            continue
        if type(resr) is not tuple:
            more_id = resr.more_id
            resr = (resr, None )
        else:
            more_id = None
        vcanvas.clear()
        # ... Thus all members of resr and all variables of rsr will be
        # plotted in the same plot...
        for rsr in resr:
            if rsr is None:
                continue
            ir += 1
            tm = tmobs[ir]
            if tmmobs != []:
                tm2 = tmmobs[ir]
            title1 = getattr( rsr, 'title1', rsr.title )
            title2 = getattr( rsr, 'title2', rsr.title )
            title = title1
            #title = rsr.title

            rsr_presentation = rsr.presentation
            for varIndex, var in enumerate(rsr.vars):
                savePNG = True
                seqsetattr(var,'title',title)
                try:
                    ftid = var.filetable.id()
                    del var.filetable  # we'll write var soon, and can't write a filetable
                    var.filetableid = ftid  # but we'll still need to know what the filetable is
                except:
                    pass
                try:
                    ft2id = var.filetable2.id()
                    del var.filetable2  # we'll write var soon, and can't write a filetable
                    var.filetable2id = ft2id  # but we'll still need to know what the filetable is
                except:
                    pass

                # ...But the VCS plot system will overwrite the title line
                # with whatever else it can come up with:
                # long_name, id, and units. Generally the units are harmless,
                # but the rest has to go....

                if seqhasattr(var,'long_name'):
                    if type(var) is tuple:
                        for v in var:
                            del v.long_name
                    else:
                        del var.long_name
                if seqhasattr(var,'id'):
                    if type(var) is tuple:   # only for vector plots
                        vname = ','.join( seqgetattr(var,'id','') )
                        vname = vname.replace(' ', '_')
                        var_id_save = seqgetattr(var,'id','')
                        seqsetattr( var,'id','' )
                    else:
                        vname = var.id.replace(' ', '_')
                        var_id_save = var.id
                        var._id = var.id
                        var.id = ''         # If id exists, vcs uses it as a plot title
                        # and if id doesn't exist, the system will create one before plotting!
                    vname = vname.replace('/', '_')
                else:
                    vname = ''
                #### TODO - Do we need the old style very verbose names here?
                #### jfp, my answer: The right way to do it is that all the verbose information
                #### useful for file names should be constructed elsewhere, perhaps in a named tuple.
                #### The verbose names are formed, basically, by concatenating everything in that
                #### tuple.  What we should do here is to form file names by concatenating the
                #### most interesting parts of that tuple, whatever they are.  But it's important
                #### to use enough so that different plots will almost surely have different names.
                #### bes - it is also a requirement that filenames be reconstructable after-the-fact
                #### with only the dataset name (the dsname parameter probably) and the combination of
                #### seasons/vars/setnames/varopts/etc used to create the plot. Otherwise, there is no
                #### way for classic viewer to know the filename without lots more special casing. 

                file_descr = getattr(rsr,'file_descr',getattr(rsr,'title2',True))
                if file_descr[0:3]=='obs': file_descr='obs'
                if file_descr[0:4]=='diff': file_descr='diff'
                if 'runby' in opts.keys() and opts['runby']=='meta':
                    # metadiags computes its own filenames using descr=True; we have to be consistent
                    descr = True
                    #try:
                    #    descr = var.filetableid.ftid
                    #    if len(descr)<1:
                    #        descr = True
                    #except:
                    #    descr = True
                else:
                    try:
                        #descr = var.filetableid.nickname
                        ft1id = var.filetableid.ftid
                        if hasattr(var,'filetable2id'):
                            ft2id = var.filetable2id.ftid
                        else:
                            ft2id = ''
                        descr = underscore_join([ft1id,ft2id,file_descr])
                        if len(descr)<1:
                            descr = True
                    except:
                        descr = True

                if 'runby' in opts.keys() and opts['runby']=='meta':
                    descr = True# For metadiags use, we want to force descr=True as it is in filenames()
                    fnamepng,fnamesvg,fnamepdf = form_filename(
                        frnamebase, ('png','svg','pdf'), descr=descr, vname=vname, more_id=more_id )
                else:
                    if file_descr=='diff':
                        modobssrc = source_descr2  # diff: both sources
                    else:
                        modobssrc = [rsr.source]     # model or obs: only current source
                    fnamepng,fnamesvg,fnamepdf = form_filename(
                        frnamebase, ('png','svg','pdf'), modobs=modobssrc, more_id=more_id )

                # Beginning of section for building plots; this depends on the plot set!
                if vcs.isscatter(rsr.presentation) or (plot.number in ['11', '12'] and package.upper() == 'AMWG'):
                    if hasattr(plot, 'customizeTemplates'):
                        if hasattr(plot, 'replaceIds'):
                            var = plot.replaceIds(var)
                        tm, tm2 = plot.customizeTemplates( [(vcanvas, tm), (vcanvas2, tm2)],
                                                           data=var, varIndex=varIndex, graphicMethod=rsr_presentation,
                                                           var=var, iteration=ir )
                    if len(rsr.vars) == 1:
                        #scatter plot for plot set 12
                        subtitle = title
                        vcanvas.plot(var,
                                     rsr_presentation, tm, bg=1, title=title,
                                     source=rsr.source)
                        savePNG = False
                        #plot the multibox plot
                        try:
                            if tm2 is not None and varIndex+1 == len(rsr.vars):
                                if hasattr(plot, 'compositeTitle'):
                                    title = plot.compositeTitle

                                # This is the Yxvsx plots from the multiplot
                                vcanvas2.plot(var,
                                              rsr_presentation, tm2, bg=1, title=title,
                                              source=subtitle)
                                if file_descr!='diff':
                                    source_descr2.append(rsr.source)
                                plotcv2 = True
                                savePNG = True
                        except vcs.error.vcsError as e:
                            logger.exception("Making summary plot: %s", e)
                            savePNG = True
                    elif len(rsr.vars) == 2:
                        if varIndex == 0:
                            #first pass through just save the array
                            xvar = var.flatten()
                            savePNG = False
                        elif varIndex == 1:
                            #second pass through plot the 2nd variables or next 2 variables
                            yvar = var.flatten()
                            if hasattr(plot, 'customizeTemplates'):
                                tm, tm2 = plot.customizeTemplates( [(vcanvas, tm), (vcanvas2, tm2)],
                                                                   data=[xvar.units, yvar.units], varIndex=varIndex,
                                                                   graphicMethod=rsr_presentation, var=var, iteration=ir)                            
                            # Scatter part from the single plots in set 11
                            vcanvas.plot(xvar, yvar,
                                         rsr_presentation, tm, bg=1, title=title,
                                         source=rsr.source )

                        #plot the multibox plot
                        try:
                            if tm2 is not None and varIndex+1 == len(rsr.vars):
                                #title refers to the title for the individual plots getattr(xvar,'units','')
                                subtitle = title
                                if hasattr(plot, 'compositeTitle'):
                                    title = plot.compositeTitle

                                # This is the scatter plots from the multiplot
                                vcanvas2.plot(xvar, yvar,
                                              rsr_presentation, tm2, bg=1, title=title,
                                              source=subtitle)
                                if file_descr!='diff':
                                    source_descr2.append(rsr.source)
                                plotcv2 = True
                                if varIndex+1 == len(rsr.vars):
                                    savePNG = True
                        except vcs.error.vcsError as e:
                            logger.exception("Making summary plot: %s", e)
                            savePNG = True
                elif vcs.isvector(rsr.presentation) or rsr.presentation.__class__.__name__=="Gv":
                    strideX = rsr.strideX
                    strideY = rsr.strideY
                    ratio="autot"
                    try:
                        lat = var[0].getLatitude()
                        if numpy.abs(lat[-1]-lat[0])>150 and ratio=="autot":
                            ratio=None
                    except:
                        pass

                    if plot.number == '6':
                        vcanvas.plot( var[0](longitude=(-10,370))[::strideY,::strideX],
                                      var[1](longitude=(-10,370))[::strideY,::strideX], rsr.presentation, tmobs[ir], bg=1, ratio=ratio)
                    else:
                        # Note that continents=0 is a useful plot option
                        vcanvas.plot( var[0](longitude=(-10,370))[::strideY,::strideX],
                                      var[1](longitude=(-10,370))[::strideY,::strideX], rsr.presentation, tmobs[ir], bg=1,
                                      title=title, units=getattr(var,'units',''), ratio=ratio,
                                      source=rsr.source )

                    # the last two lines shouldn't be here.  These (title,units,source)
                    # should come from the contour plot, but that doesn't seem to
                    # have them.
                    try:
                        if tm2 is not None:
                            vcanvas2.plot( var[0](longitude=(-10,370))[::strideY,::strideX],
                                           var[1](longitude=(-10,370))[::strideY,::strideX],
                                           rsr.presentation, tm2, bg=1,
                                           title=title, units=getattr(var,'units',''),
                                           ratio=ratio )
                                          # the contour part of the plot does this: source=rsr.source
                            # the last two lines shouldn't be here.  These (title,units,source)
                            # should come from the contour plot, but that doesn't seem to
                            # have them.
                            if file_descr!='diff':
                                source_descr2.append(rsr.source)
                    except vcs.error.vcsError as e:
                        logger.exception("Making summary plot: %s", e)
                elif vcs.istaylordiagram(rsr.presentation):
                    # this is a total hack that is related to the hack in uvdat.py
                    try:
                        vcanvas.legendTitles = rsr.legendTitles
                    except:  # Recently the above has failed because vcanvas doesn't have the attribute legendTitles.
                        pass
                    if hasattr(plot, 'customizeTemplates'):
                        vcanvas.setcolormap("bl_to_darkred")

                        tm, tm2 = plot.customizeTemplates( [(vcanvas, tm), (None, None)],
                                                           legendTitles=rsr.legendTitles )
                    vcanvas.plot(var, rsr.presentation, tm, bg=1,
                                 title=title, units=getattr(var,'units',''), source=rsr.source )
                    savePNG = True
                    #rsr.presentation.script("jim_td")
                    # tm.script("jim_tm")
                    # fjim=cdms2.open("jim_data.nc","w")
                    # fjim.write(var,id="jim")
                    # fjim.close()
                else:
                    # Set canvas colormap back to default color
                    # Formerly we did it this way:
                    #  vcanvas2.setcolormap('bl_to_darkred')
                    # But that redraws everything already drawn before, and does it with the wrong title.
                    # And it's a drag on performance.
                    # All setcolormap() does is the following line, which we want; plus an
                    # update() line which we don't want.
                    vcanvas2.colormap = 'bl_to_darkred'

                    #check for units specified for display purposes
                    var_save = var.clone()
                    if displayunits != None:
                        if isinstance(displayunits,(list,tuple)):
                            if len(displayunits)>1:
                                logger.warning("multiple displayunits not supported at this time, using: %s" % displayunits[0])
                            displayunits=displayunits[0]
                        try:
                            var = convert_units(var,displayunits)
                        except:
                            try:
                                scale = float(displayunits)
                            except:
                                logger.critical('Invalid display units: '+ displayunits)
                                sys.exit()
                        var.id = '' #this was clearer earlier; var=anything makes an id

                    if hasattr(plot, 'customizeTemplates'):
                        tm, tm2 = plot.customizeTemplates( [(vcanvas, tm), (vcanvas2, tm2)], data=var,
                                                           varIndex=varIndex, graphicMethod=rsr.presentation,
                                                           var=var, uvcplotspec=rsr )
                    # Single plot
                    t0 = time.time()
                    plot.vcs_plot(vcanvas, var(longitude=(-10,370)), rsr.presentation, tm, bg=1,
                                  title=title1, source=rsr.source,
                                  plotparms=getattr(rsr,'plotparms',None) )
                    tp += time.time() - t0
#                                      vcanvas3.clear()
#                                      vcanvas3.plot(var, rsr.presentation )
                    savePNG = True

                    # Multi-plot
                    try:
                        if tm2 is not None:
                            # Multiple plots on a page:
                            t0 = time.time()
                            plot.vcs_plot( vcanvas2, var(longitude=(-10,370)), rsr.presentation, tm2, bg=1,
                                           title=title2, source=rsr.source,
                                           plotparms=getattr(rsr,'plotparms',None))#,
                                           #compoundplot=onPage )
                            tp += time.time() - t0
                            if file_descr!='diff':
                                source_descr2.append(rsr.source)
                            plotcv2 = True

                    except vcs.error.vcsError as e:
                        logger.exception("Making summary plot: %s", e)
                    #restore var before the KLUDGE!!!
                    var = var_save                

                # End of section for building plots

                if hasattr(var, 'model') and hasattr(var, 'obs'):
                    delattr(var, 'model')
                    delattr(var, 'obs')
                rsr.vars[varIndex] = var

                if var_id_save is not None:
                    if type(var_id_save) is str:
                        var.id = var_id_save
                    else:
                        for i in range(len(var_id_save)):
                            var[i].id = var_id_save[i]
        if savePNG:
            t0 = time.time()
            vcanvas.png( fnamepng, ignore_alpha=True, metadata=provenance_dict() )
            t1 += time.time() - t0
                # vcanvas.svg() doesn't support ignore_alpha or metadata keywords
                #vcanvas.svg( fnamesvg )
                #vcanvas.pdf( fnamepdf)

    if tmmobs[0] is not None:  # If anything was plotted to vcanvas2
        vname = varid.replace(' ', '_')
        vname = vname.replace('/', '_')
    if 'runby' in opts.keys() and opts['runby']=='meta':
        descr = True# For metadiags use, we want to force descr=True as it is in filenames()
        fnamepng,fnamesvg,fnamepdf = form_filename( frnamebase, ('png','svg','pdf'),
                                                    descr=descr, vname=vname, more_id='combined' )
    else:
        source_descr2 = list(set(source_descr2))
        fnamepng,fnamesvg,fnamepdf = form_filename( frnamebase, ('png','svg','pdf'),
                                                    modobs=list(source_descr2), more_id='combined' )

    if vcanvas2.backend.renWin is None:
        logger.warning("no data to plot to file2: %s", fnamepng)
    else:
        logger.info("writing png file2: %s",fnamepng)
        t0 = time.time()
        vcanvas2.png( fnamepng , ignore_alpha = True, metadata=provenance_dict())
        t1 += time.time() - t0
        #logger.info("writing svg file2: %s",fnamesvg)
        # vcanvas2.svg() doesn't support ignore_alpha or metadata keywords
        #vcanvas2.svg( fnamesvg )
        #logger.info("writing pdf file2: %s",fnamepdf)
        #vcanvas2.pdf( fnamepdf )            
    print "In makeplots, variable",vname,"running time for making plots:",tp
    print "In makeplots, variable",vname,"running time for writing png files:",t1
    print "Makeplots, variable",vname,"total time is",time.time()-tt0

if __name__ == '__main__':
    print "UV-CDAT Diagnostics, command-line version"
    print ' '.join(sys.argv)
    try:
        irunby = sys.argv.index('--runby')
        runby = sys.argv[irunby+1]
        o = Options(runby=runby)
    except ValueError:
        o = Options()
    o.parseCmdLine()
    o.verifyOptions()
    #print o._opts['levels']
    #print o._opts['displayunits']
    run_diags(o)
