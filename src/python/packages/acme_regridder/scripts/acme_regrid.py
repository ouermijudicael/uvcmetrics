#!/usr/bin/env python
# Import regrid2 package for regridder functions
import cdms2 
import sys, os
import numpy
import MV2
import argparse
import metrics
import metrics.packages.acme_regridder._regrid
import cdutil
import datetime


class WeightFileRegridder:
  def __init__(self,weightFile,toRegularGrid=True):
    if isinstance(weightFile,str):
      if not os.path.exists(weightFile):
        raise Exception("WeightFile %s does not exists" % weightFile)
      wFile=cdms2.open(weightFile)
    else:
      wFile = weightFile
    self.S=wFile("S").filled()
    self.row=wFile("row").filled()-1
    self.col=wFile("col").filled()-1
    self.mask_b = wFile("mask_b")
    self.nb = self.mask_b.shape[0]
    if self.mask_b.min() == 1 and self.mask_b.max()==1:
      print "No need to maks after"
      self.mask_b = False
    else:
      print "MX MIN:",self.mask_b.max(),self.mask_b.min()
      self.mask_b=numpy.logical_not(wFile("mask_b").filled())
    self.n_s=self.S.shape[0]
    self.method = wFile.map_method
    self.regular=toRegularGrid
    if toRegularGrid:
      self.lats=cdms2.createAxis(sorted(set(wFile("yc_b").tolist())))
      self.lats.designateLatitude()
      self.lats.units="degrees_north"
      self.lats.setBounds(None)
      self.lats.id="lat"
      self.lons=cdms2.createAxis(sorted(set(wFile("xc_b").tolist())))
      self.lons.designateLongitude()
      self.lons.units="degrees_east"
      self.lons.setBounds(None)
      self.lons.id="lon"
    else:
      self.yc_b=wFile("yc_b")
      self.xc_b=wFile("xc_b")
      self.yv_b=wFile("yv_b")
      self.xv_b=wFile("xv_b")

    if isinstance(weightFile,str):
        wFile.close()

  def regrid(self, input):
    axes=input.getAxisList()
    input_id=input.id
    M = input.getMissing()
    if input.mask is numpy.ma.nomask:
      isMasked = False
      input=input.data
    else:
      isMasked = True
      input=input.filled(float(M))
    sh=input.shape
    if isMasked:
      dest_field = metrics.packages.acme_regridder._regrid.apply_weights_masked(input,self.S,self.row,self.col,self.nb,float(M))
    else:
      dest_field = metrics.packages.acme_regridder._regrid.apply_weights(input,self.S,self.row,self.col,self.nb)
    if self.mask_b is not False:
     print "Applying mask_b"
     dest_field=numpy.ma.masked_where(self.mask_b,dest_field)
    if self.regular:
      sh2=list(sh[:-1])#+[len(self.lats),len(self.lons)]
      sh2.append(len(self.lats))
      sh2.append(len(self.lons))
      dest_field.shape=sh2
      dest_field=MV2.array(dest_field,id=input_id)
      dest_field.setAxis(-1,self.lons)
      dest_field.setAxis(-2,self.lats)
      for i in range(len(sh2)-2):
        dest_field.setAxis(i,axes[i])
      if isMasked:
        dest_field.setMissing(M)
    return dest_field

def addAxes(f,axisList):
  axes = []
  for ax in axisList:
    if not ax.id in f.listdimension():
      A = f.createAxis(ax.id,ax[:])
      for att in ax.attributes:
        setattr(A,att,getattr(ax,att))
    else:
      A = f.getAxis(ax.id)
    axes.append(A)
  return axes

def addVariable(f,id,typecode,axes,attributes):
  axes = addAxes(f,axes)
  V = f.createVariable(id,typecode,axes)
  for att in attributes:
    setattr(V,att,attributes[att])


if __name__=="__main__":

  cdms2.setNetcdfClassicFlag(1)
  #cdms2.setNetcdf4Flag(0)
  cdms2.setNetcdfShuffleFlag(0)
  cdms2.setNetcdfDeflateFlag(0)
  cdms2.setNetcdfDeflateLevelFlag(0)

  ## Create the parser for user input
  parser = argparse.ArgumentParser(description='Regrid variables in a file using a weight file')
  parser.add_argument("--input","-i","-f","--file",dest="file",help="input file to process",required=True)
  parser.add_argument("--weight-file","-w",dest="weights",help="path to weight file",required=True)
  parser.add_argument("--output","-o",dest="out",help="output file")
  parser.add_argument("--var","-v",dest="var",nargs="*",help="variable to process (default is all variable with 'ncol' dimension")

  args = parser.parse_args(sys.argv[1:])

  print args
  # Read the weights file
  regdr = WeightFileRegridder(args.weights)

  f=cdms2.open(args.file)

  if args.out is None:
    onm = ".".join(args.file.split(".")[:-1])+"_regrid.nc"
  else:
    onm = args.out
  print "Output file:",onm
  fo=cdms2.open(onm,"w")
  history = ""
  ## Ok now let's start by copying the attributes back onto the new file
  for a in f.attributes:
    if a!="history":
      setattr(fo,a,getattr(f,a))
    else:
      history=getattr(f,a)+"\n"
  history+="%s: weights applied via acme_regrid (git commit: %s), created by %s from path: %s with input command line: %s" % (str(datetime.datetime.utcnow()),metrics.git.commit,os.getlogin(),os.getcwd()," ".join(sys.argv))
  fo.history=history 
  
  wgt = None
  if args.var is not None:
    vars=args.var
  else:
    vars= f.variables.keys()
  axes3d = []
  axes4d = []
  wgts = None
  area = None
  NVARS = len(vars)
  for i,v in enumerate(vars):
    V=f[v]
    if V is None:
      print "Will skip",V,"as it does NOT appear to be in file"
    elif V.id in ["lat","lon","area"]:
      print i,NVARS,"Will skip",V.id,"no longer needed or recomputed"
    elif "ncol" in V.getAxisIds():
      print i,NVARS,"Will process:",V.id
      if V.rank()==2:
        if axes3d == []:
          dat2 = cdms2.MV2.array(regdr.regrid(V()))
          axes3d = dat2.getAxisList()
        addVariable(fo,V.id,V.typecode(),axes3d,V.attributes)
      elif V.rank()==3:
        if axes4d == []:
          dat2 = cdms2.MV2.array(regdr.regrid(V()))
          axes4d = dat2.getAxisList()
        addVariable(fo,V.id,V.typecode(),axes4d,V.attributes)
      if wgt is None:
        wgt = True
        addVariable(fo,"wgt","d",[dat2.getLatitude(),],[])
        addVariable(fo,"area","f",[dat2.getLatitude(),dat2.getLongitude()],[])
    else:
      print "Will rewrite as is",V.id
      addVariable(fo,V.id,V.typecode(),V.getAxisList(),V.attributes)

  wgts = None
  for i,v in enumerate(vars):
    V=f[v]
    if V is None:
      print "Skipping",V,"as it does NOT appear to be in file"
    elif V.id in ["lat","lon","area"]:
      print i,NVARS,"Skipping",V.id,"no longer needed or recomputed"
    elif "ncol" in V.getAxisIds():
      print i,NVARS,"Processing:",V.id
      V2=fo[V.id]
      dat2 = cdms2.MV2.array(regdr.regrid(V()))
      V2[:]=dat2[:]
      #fo.sync()
      if wgts is None:
        print "trying to get weights"
        wgts = [ numpy.sin(x[1]*numpy.pi/180.)-numpy.sin(x[0]*numpy.pi/180.) for x in dat2.getLatitude().getBounds()]
        #wgts.setMissing(1.e20)
        V2=fo["wgt"]
        V2[:]=wgts[:]
        #fo.sync()
        if dat2.ndim>3:
            dat2=dat2[0,0]
        else:
            dat2=dat2[0]
        print "Computing area weights"
        area = cdutil.area_weights(dat2).astype("f")
        V2=fo["area"]
        V2[:]=area[:]
        #fo.sync()
    else:
      print i,NVARS,"Rewriting as is:",V.id
      try:
        V2=fo[V.id]
        V2[:]=V[:]
        #fo.sync()
      except:
        pass
  fo.close()
