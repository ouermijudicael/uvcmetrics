#!/usr/bin/env python
"""" In this file the inputs for the test are defined and passed to diags_test.execute"""
import diags_test
from metrics.packages.amwg.amwg import amwg_plot_set7

print amwg_plot_set7.name

test_str = 'Test 07\n'
#run this from command line to get the files required
example = "./diagtest07.py --datadir ~/uvcmetrics_test_data/ --baseline ~/uvcdat-testdata/baselines/metrics/ --keep True"

plotset = 7
filterid = 'f_contains'
obsid = 'NCEP'
varid = 'T'
seasonid = 'ANN'
modeldir = 'cam_output'
obsdir = 'obs_atmos'
dt = diags_test.DiagTest( modeldir, obsdir, plotset, filterid, obsid, varid, seasonid )

# Test of graphics (png) file match:
# This just looks at combined plot, aka summary plot, which is a compound of three plots.
imagefilename = 'set7_ANN_T-combined-NCEP_b30.009.png'
imagethreshold = None
ncfiles = {}
#ncfiles['rv_T_ANN_ft1_cam_output_ANN_cam_output.nc'] = ['rv_T_ANN_ft1_cam_output']
#ncfiles['rv_T_ANN_ft2_obs_atmos_NCEP_ANN_obs_atmos_NCEP.nc'] = ['rv_T_ANN_ft2_obs_atmos_NCEP']
ncfiles['set7_ANN_T-cam_output_model.nc'] = ['rv_T_ANN_Global_cam_output']
ncfiles['set7_ANN_T-NCEP_obs.nc'] = ['rv_T_ANN_Global_NCEP']

# Test of NetCDF data (nc) file match:
rtol = 1.0e-3
atol = 1.0e-2   # suitable for temperatures

dt.execute(test_str, imagefilename, imagethreshold, ncfiles, rtol, atol)
