'''

    picty
    Copyright (C) 2013  Damien Moore

License:

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

'''
metadata2.py

This module describes the subset of exif, iptc and xmp metadata used by the program
and provides a dictionary to handle conversion between pyexiv2 formats and the internal
representation (works with pyexiv2 version 0.2 and later)
'''

import pyexiv2
import gtk
import tempfile
from picty.fstools import io
import os.path
import json

import time
import datetime

from picty import settings
#Fallback for windows or cases where Gtk can't handle the image
from PIL import Image
from PIL import ImageFile
import io as _io


pyexiv2.register_namespace('http://www.picty.net/xmpschema/1.0/','picty')
##todo: need to improve xmp support such as synchronizing tags across the Iptc
##e.g. merge Iptc.Application2.Keywords with Xmp.dc.subject


class Exiv2Metadata(pyexiv2.ImageMetadata):
    def __init__(self,filename):
        pyexiv2.ImageMetadata.__init__(self,filename)
    def __setitem__(self,key,value):
        try:
            pyexiv2.ImageMetadata.__setitem__(self,key,value)
        except TypeError:
            if key.startswith('Exif'):
                value=pyexiv2.ExifTag(key,value)
            elif key.startswith('Iptc'):
                value=pyexiv2.IptcTag(key,value)
            elif key.startswith('Xmp'):
                value=pyexiv2.XmpTag(key,value)
            pyexiv2.ImageMetadata.__setitem__(self,key,value)

def extract_thumbnail_from_metadata(item, rawmeta):
    previews = rawmeta.previews
    if previews:
        print 'opening preview',len(previews)
        preview_ind = len(previews)-1
        while True:
            try:
                try:
                    if settings.is_windows: #something is missing from GTK+ on windows -- prevents PixbufLoader from reading the preview images
                        raise IOError('PixbufLoader not available on windows')
                    pbloader = gtk.gdk.PixbufLoader()
                    pbloader.write(previews[preview_ind].data)
                    pb = pbloader.get_pixbuf()
                    pbloader.close()
                    w=pb.get_width()
                    h=pb.get_height()
                    a=max(128,w,h)
                    item.thumb=pb.scale_simple(128*w/a,128*h/a,gtk.gdk.INTERP_BILINEAR)
                    break
                except: ##Mostly a workaround for DNGs or Windows PCs
                    im = Image.open(_io.BytesIO(previews[preview_ind].data))
#                    p = ImageFile.Parser()
#                    p.feed(previews[preview_ind].data)
#                    im = p.close()
                    from picty import imagemanip
                    im.thumbnail((128,128),Image.ANTIALIAS)
                    item.thumb = imagemanip.image_to_pixbuf(im)
                    break
            except:
                if preview_ind == 0:
                    raise
                preview_ind = 0
    else:
        print 'No usable thumbnail data for', item
        item.thumb=False
        return False

def load_metadata(item=None,filename=None,thumbnail=False,missing_only=False):
    '''
    load the metadata from the image and convert the keys to a subset that picty understands
    item - the item to load metadata for
    filename - if specified, metadata is loaded from this file
    thumbnail - if True, load the thumbnail from the file
    missing_only - if True, will set keys that aren't already present in the item
    '''
    t=time.time()
    try:
        if not filename:
            filename=item.uid
        rawmeta = Exiv2Metadata(filename)
        rawmeta.read()
        meta={}
        get_exiv2_meta(meta,rawmeta)
        if missing_only and item.meta!=None:
            for k in meta:
                if k not in item.meta:
                    item.meta[k] = meta[k]
        else:
            item.meta=meta
        if thumbnail:
            try:
                extract_thumbnail_from_metadata(item, rawmeta)
            except:
                print 'Load thumbnail failed for',item.uid
                import traceback,sys
                print traceback.format_exc(sys.exc_info()[2])
                item.thumb=False
    except:
        print 'Error reading metadata for',filename
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
        if item.meta is None:
            item.meta={}
        return False
    item.mark_meta_saved()
    print 'read took',time.time()-t
    return True


def load_thumbnail(item=None,filename=None):
    '''
    load the metadata from the image and convert the keys to a subset that picty understands
    item - the item to load metadata for
    filename - if specified, metadata is loaded from this file
    thumbnail - if True, load the thumbnail from the file
    missing_only - if True, will set keys that aren't already present in the item
    '''
    try:
        if not filename:
            filename=item.uid
        rawmeta = Exiv2Metadata(filename)
        rawmeta.read()
        try:
            extract_thumbnail_from_metadata(item, rawmeta)
        except:
            print 'Load thumbnail failed for',item.uid
            import traceback,sys
            print traceback.format_exc(sys.exc_info()[2])
            item.thumb=False
    except:
        print 'Error reading metadata for',filename
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
        return False
    return True


def save_metadata(item,filename):
    '''
    write the metadata in item to the underlying file converting keys from the picty representation to the relevant standard
    '''
    try:
        print 'Writing metadata for',item.uid
        if filename.lower().endswith('.crw'):
            raise IOError("Writing to CRW is not supported. Enable sidecars in collection settings to save your changes.")
        rawmeta = Exiv2Metadata(filename)
        rawmeta.read()
        meta=item.meta.copy()
        set_exiv2_meta(meta,rawmeta) #TODO: Only want to write the metadata of the keys that have changed
        rawmeta.write()
        item.mark_meta_saved()
    except:
        print 'Error writing metadata for',item.uid
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
        return False
    return True

sidecar_stub='''<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
 </rdf:RDF>
</x:xmpmeta>'''

def create_sidecar(item,src,dest):
    try:
        im = pyexiv2.ImageMetadata(src)
        im.read()
        src_data=True
    except:
        src_data=False
        print 'Creating sidecar: source image %s does not have readable metadata'%(src)

    try:
        print 'Creating sidecar for',item.uid
        f = open(dest,'wb')
        f.write(sidecar_stub)
        f.close()
    except:
        print 'Error creating sidecar stub for',item,'with filename',dest
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
        return False
    try:
        if src_data:
            sidecar = pyexiv2.ImageMetadata(dest)
            sidecar.read()
            im.copy(sidecar,comment=False)
            sidecar.write()
    except:
        print 'Error copying metadata to sidecar for',item,'with filename',dest
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
        print 'Using empty sidecar'
        return True
    return True

def load_sidecar(item,filename,missing_only=False, image_filename = None):
    '''
    Loads metadata for the image `item` from the sidecar in the path `filename`.
    If `image_filename` is specified, metadata will first be filled by
    loading the metadata from that image before updating the `item` with the metadata
    in the sidecar. If `missing_only` is specified, then the only those fields that aren't
    already present will be added.
    '''
    try:
        meta={}
        if image_filename is not None:
            rawmeta = Exiv2Metadata(image_filename)
            rawmeta.read()
            get_exiv2_meta(meta, rawmeta)
        print 'Loading sidecar for',item
        rawmeta = Exiv2Metadata(filename)
        rawmeta.read()
        get_exiv2_meta(meta, rawmeta, apptags_dict_sidecar)
        if missing_only and item.meta!=None:
            for k in meta:
                if k not in item.meta:
                    item.meta[k] = meta[k]
        else:
            item.meta=meta
    except:
        print 'Error reading sidecar from',filename
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
        if item.meta is None:
            item.meta={}
        return False
    item.mark_meta_saved()
    return True

def save_sidecar(item,filename):
    '''
    write the metadata in item to the underlying file converting keys from the picty representation to the relevant standard
    '''
    try:
        print 'Creating sidecar for',item.uid,'filename',filename
        rawmeta = Exiv2Metadata(filename)
        rawmeta.read()

        workaround=True
        if workaround:
            tdir,tname=os.path.split(filename)
            h,tfilename=tempfile.mkstemp('',tname,tdir)
            print 'Writing sidecar using workaround, tempfile name was',tfilename
            f=open(tfilename,'wb')
            f.write(sidecar_stub)
            f.close()
            rawmeta_out = Exiv2Metadata(tfilename)
            rawmeta_out.read()

        meta=item.meta.copy()
        set_exiv2_meta(meta,rawmeta,apptags_dict_sidecar) #TODO: Only want to write the metadata of the keys that have changed
        if workaround:
            rawmeta.copy(rawmeta_out,exif=False, iptc=False, xmp=True,comment=False)
            rawmeta_out.write()
            io.move_file(tfilename,filename,overwrite=True)
        else:
            rawmeta.write()
        item.mark_meta_saved()
    except:
        print 'Error writing sidecar for',item.uid
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
        return False
    return True

def copy_metadata(src_meta,src_file,destination_file):
    '''
    copy metadata from a source item to a destination file
    due to bugs in pyexiv2|exiv2, only the metadata in the
    module list 'apptags' are written
    '''
    if src_meta==False:
        return False
    try:
        rawmeta_src = Exiv2Metadata(src_file)
        rawmeta_src.read()
    except:
        print 'Error reading source prior to copying metadata for source file',src_file
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
        return False
    try:
        rawmeta_dest = Exiv2Metadata(destination_file)
        rawmeta_dest.read()
        rawmeta_src.copy(rawmeta_dest)
        set_exiv2_meta(src_meta,rawmeta_dest)
        rawmeta_dest.write()
    except:
        print 'Error copying metadata to destination file',destination_file
        import traceback,sys
        print traceback.format_exc(sys.exc_info()[2])
    return True


def save_metadata_key(filename,key,value):
    '''
    write a single exiv2 native key value to the image file associated with item
    '''
    try:
        rawmeta = Exiv2Metadata(filename)
        rawmeta.read()
        rawmeta[key]=value
        rawmeta.write()
    except:
        print 'Error writing metadata for',filename




##The conv functions take a key and return a string representation of the metadata OR if value!=None convert the string value to a set of (metadata_key,value) tag pairs

def conv_date_taken(metaobject,keys,value=None):
    if value!=None: #todo: this should copy the local representation back to the image metadata
        return True
    date=None
###    if "Iptc.Application2.DateCreated" in metaobject.exif_keys and "Iptc.Application2.TimeCreated" in metaobject.exif_keys:
###        date=str(metaobject["Iptc.Application2.DateCreated"])+' '+str(metaobject["Iptc.Application2.TimeCreated"])
###        date=datetime.strptime(date)
    if "Exif.Photo.DateTimeOriginal" in metaobject.exif_keys:
        date=metaobject["Exif.Photo.DateTimeOriginal"].value
        if type(date)==str:
            date=datetime.strptime(date)
    elif "Exif.Photo.DateTimeDigitized" in metaobject.exif_keys: #fallback to other datetime Exif keys
        date=metaobject["Exif.Photo.DateTimeDigitized"].value
        if type(date)==str:
            date=datetime.strptime(date)
    elif "Exif.Image.DateTimeOriginal" in metaobject.exif_keys:
        date=metaobject["Exif.Image.DateTimeOriginal"].value
        if type(date)==str:
            date=datetime.strptime(date)
    elif "Exif.Image.DateTime" in metaobject.exif_keys:
        date=metaobject["Exif.Image.DateTime"].value
        if type(date)==str:
            date=datetime.strptime(date)
    return date

def conv_date_taken_xmp(metaobject,keys,value=None):
    if value!=None: #todo: this should copy the local representation back to the image metadata
        return True
    date=None
    if keys[0] in metaobject.xmp_keys:
        date=metaobject[keys[0]].raw_value
        if type(date)==str:
            date=datetime.datetime.strptime(date,'%Y-%m-%dT%H:%M:%S')
    elif keys[1] in metaobject.xmp_keys: #fallback to other datetime Exif keys
        date=metaobject[keys[1]].raw_value
        if type(date)==str:
            date=datetime.datetime.strptime(date,'%Y-%m-%dT%H:%M:%S')
    return date

def conv_str(metaobject,keys,value=None):
    if value!=None:
        if keys[0] in metaobject.xmp_keys or keys[0] in metaobject.iptc_keys or keys[0] in metaobject.exif_keys or value!='':
            if keys[0].startswith('Iptc'):
                metaobject[keys[0]]=[value]
            else:
                metaobject[keys[0]]=value
        ##todo: change or empty other keys
        return True
    for k in keys:
        try:
            if k.startswith('Iptc'):
                val=metaobject[k].values[0]
            else:
                val=metaobject[k].value
            return str(val)
        except:
            pass
    return None

def conv_str_alt(metaobject,keys,value=None):
    if value!=None:
        try:
            if keys[0] in metaobject.xmp_keys or value!='':
                metaobject[keys[0]]=value
            if keys[1] in metaobject.iptc_keys or value!='':
                metaobject[keys[1]]=[value]
            if keys[2] in metaobject.exif_keys or value!='':
                metaobject[keys[2]]=value
        except:
            pass
        return True
    for k in keys:
        try:
            if k.startswith('Iptc'):
                val=metaobject[k].values[0]
            elif k.startswith('Xmp'):
                val=metaobject[k].value['x-default']
            else:
                val=metaobject[k].value
            return str(val)
        except:
            pass
    return None


def conv_lang_alt(metaobject,keys,value=None):
    if value!=None:
        if keys[0] in metaobject.xmp_keys or value!='':
            metaobject[keys[0]]=value
        return True
    for k in keys:
        try:
            val=metaobject[k].value['x-default']
            return str(val)
        except:
            pass
    return None


def conv_int(metaobject,keys,value=None):
    if value!=None:
        if keys[0] in metaobject.xmp_keys or keys[0] in metaobject.iptc_keys or keys[0] in metaobject.exif_keys or value!=-1:
            if keys[0].startswith('Iptc'):
                metaobject[keys[0]]=[value]
            else:
                metaobject[keys[0]]=value
        ##todo: change or empty other keys
        return True
    for k in keys:
        try:
            if k.startswith('Iptc'):
                val=metaobject[k].values[0]
            else:
                val=metaobject[k].value
            return int(val)
        except:
            pass
    return None

def tag_split(tag_str,sep=' '):
    quoted=False
    tags=[]
    curtag=''
    for x in tag_str:
        if x=='"':
            quoted=not quoted
            if quoted and curtag:
                tags.append(curtag)
                curtag=''
            continue
        if (x==sep or x=='\n') and not quoted:
            if curtag:
                tags.append(curtag)
                curtag=''
            continue
        curtag+=x
    if curtag:
        tags.append(curtag)
    return tags

def tag_bind(tags,sep=' '):
    pretag=[]
    for tag in tags:
        if ' ' in tag:
            tag='"%s"'%(tag,)
        pretag.append(tag)
    return sep.join(pretag)

def conv_keywords(metaobject,keys,value=None):
    '''
    converts the Keyword metadata field to/from xmp or iptc key
    will also read from the exif tag as a fallback
    Note that this will overwrite the relevant Iptc and Xmp fields (as specified in apptags)
    '''
    if value!=None:
        if keys[0] in metaobject.xmp_keys or keys[1] in metaobject.iptc_keys or len(value)>0:
            metaobject[keys[0]]=value
            metaobject[keys[1]]=value
        return True
    try:
        val=None
        if keys[0] in metaobject.xmp_keys:
            val=metaobject[keys[0]].value
        elif keys[1] in metaobject.iptc_keys:
            val=metaobject[keys[1]].values
        if type(val)==str: ##todo: shouldn't need this check with the new pyexiv2 api
            return [val]
        return list(val)
    except:
        return None ##the fallback to UserComment is disabled for now
        try:
            #parse 'abc "def ghi" fdg' as three tags in a list
            val=metaobject["Exif.Photo.UserComment"].value ##TODO: apparently, this object is not a string, but a bytestream! Need to do conversion + encoding detection - YUK!
            vals=tag_split(val)
            return vals
        except:
            return None

def conv_keywords_xmp(metaobject,keys,value=None):
    '''
    converts the Keyword metadata field to/from xmp or iptc key
    will also read from the exif tag as a fallback
    Note that this will overwrite the relevant Iptc and Xmp fields (as specified in apptags)
    '''
    if value!=None:
        if keys[0] in metaobject.xmp_keys or len(value)>0:
            metaobject[keys[0]]=value
        return True
    try:
        val=None
        if keys[0] in metaobject.xmp_keys:
            val=metaobject[keys[0]].value
        if type(val)==str: ##todo: shouldn't need this check with the new pyexiv2 api
            return [val]
        return list(val)
    except:
        return None ##the fallback to UserComment is disabled for now


def tag_split_c(t):
    return tag_split(t,',')

def tag_bind_c(t):
    return tag_bind(t,',')

def conv_artist(metaobject,keys,value=None):
    '''
    converts the Artist metadata field to/from xmp or iptc key
    will also read from the exif tag as a fallback
    Note that this will overwrite the relevant Iptc and Xmp fields (as specified in apptags)
    '''
    if value!=None:
        if keys[0] in metaobject.xmp_keys or keys[1] in metaobject.iptc_keys or len(value)>0:
            metaobject[keys[0]]=value
            metaobject[keys[1]]=[tag_bind_c(value)] #Apparently IPTC Credit tag is not repeatable
        return True
    try:
        val=None
        if keys[0] in metaobject.xmp_keys:
            val=metaobject[keys[0]].value
        elif keys[1] in metaobject.iptc_keys:
            val=tag_split_c(metaobject[keys[1]].values)
        elif keys[2] in metaobject.exif_keys:
            val=metaobject[keys[2]].value
        if type(val)==str: #todo: shouldn't need this check with the new pyexiv2 api
            return [val]
        return list(val)
    except:
        return None

def conv_list_xmp(metaobject,keys,value=None):
    '''
    converts the Artist metadata field to/from xmp or iptc key
    will also read from the exif tag as a fallback
    Note that this will overwrite the relevant Iptc and Xmp fields (as specified in apptags)
    '''
    if value!=None:
        if keys[0] in metaobject.xmp_keys:
            metaobject[keys[0]]=value
        return True
    try:
        val=None
        if keys[0] in metaobject.xmp_keys:
            val=metaobject[keys[0]].value
        if type(val)==str: #todo: shouldn't need this check with the new pyexiv2 api
            return [val]
        return list(val)
    except:
        return None


def conv_rational(metaobject,keys,value=None):
    if value!=None:
        if keys[0] in metaobject.iptc_keys or keys[0] in metaobject.exif_keys and len(value)>0:
            ##todo: change or empty other keys
            try:
                if type(value)==str:
                    metaobject[keys[0]]=value
                    return True
                if type(value)==tuple:
                    metaobject[keys[0]]=pyexiv2.Rational(value[0],value[1])
                    return True
            except:
                pass
        return True
    for k in keys:
        try:
            val=metaobject[k].value
            return (val.numerator,val.denominator)
        except:
            pass
    return None

def coords_as_rational(decimal):
    decimal=abs(decimal)
    degree=int(decimal)
    minute=int((decimal-degree)*60)
    second=int((decimal-degree)*3600-minute*60)
    return (pyexiv2.Rational(degree,1),pyexiv2.Rational(minute,1),pyexiv2.Rational(second,1))

def coords_as_decimal(rational):
    print 'coords',rational
    print 'type coords',type(rational)
    print 'len coords',len(rational)
    try:
        if len(rational)>0:
            deci=1.0*rational[0].numerator/rational[0].denominator
            if len(rational)>1:
                deci+=1.0*rational[1].numerator/rational[1].denominator/60
            if len(rational)>2:
                deci+=1.0*rational[2].numerator/rational[2].denominator/3600
            return deci
    except:
        if type(rational) == pyexiv2.Rational:
            return 1.0*rational.numerator/rational.denominator
    raise TypeError

def conv_latlon(metaobject,keys,value=None):
    if value!=None:
        print 'latlon setting',value
        lat,lon=value
        rat_lat=coords_as_rational(lat)##(int(abs(lat)*1000000),1000000)
        rat_lon=coords_as_rational(lon)##(int(abs(lon)*1000000),1000000)
        latref='N' if lat>=0 else 'S'
        lonref='E' if lon>=0 else 'W'
        print 'latlon setting',rat_lat,rat_lon,latref,lonref
        metaobject[keys[0]]=rat_lat
        metaobject[keys[1]]=latref
        metaobject[keys[2]]=rat_lon
        metaobject[keys[3]]=lonref
        return True
    else:
        try:
            rat_lat=metaobject[keys[0]].value
            latref=metaobject[keys[1]].value
            rat_lon=metaobject[keys[2]].value
            lonref=metaobject[keys[3]].value
            lat=(1.0 if latref=='N' else -1.0)*coords_as_decimal(rat_lat)
            lon=(1.0 if lonref=='E' else -1.0)*coords_as_decimal(rat_lon)
            return (lat,lon)
        except KeyError:
            return None
        except:
            print 'Error setting geolocation in exiv2'
            import traceback,sys
            print traceback.format_exc(sys.exc_info()[2])
            return None

def conv_image_transforms(metaobject,keys,value=None):
    if value!=None:
        str_transforms = json.dumps(value)
        metaobject[keys[0]]=str_transforms
    else:
        try:
            result = json.loads(metaobject[keys[0]].value)
            return result
        except:
            #todo: log the error
            return None

def tup2str(value):
    try:
        return '%3.6f;%3.6f'%value
    except:
        return ''

def str2tup(value):
    vals=value.split(';')
    return (float(vals[0]),float(vals[1]))

def str2rat(value):
    vals=value.split('/')
    return (int(vals[0]),int(vals[1]))

def rat2str(value):
    return '%i/%i'%value

def rational_as_float(value_tuple):
    return 1.0*value_tuple[0]/value_tuple[1]

def date_as_sortable(date_value):
    if date_value:
        return date_value
    return datatime.date(1900,1,1)

'''
apptags defines the exif metadata kept in the cache.
the data are created from and written to the item.
each entry in the tuple is itself a tuple containing:
 * The short name of the tag (to be used in the program)
 * The display name of the tag
 * User Editable (TRUE/FALSE) in a gtk.Entry
 * The callback to convert to the container format (exiv2) and the
    preferred representation of this app (tuple, str, datetime, int, float)
 * A function to convert the internal rep to a string
 * A function to convert a string to the internal rep
 * A function to convert the key to a sortable
 * A tuple of EXIF, IPTC and XMP tags from which to fill the app tag (passed to the callback)
 * A boolean indicator of whether to delete these keys when writing metadata if they aren't present in the appmeta
'''

apptags=(
("DateTaken","Date Taken",False,conv_date_taken,None,None,date_as_sortable,(("Iptc.Application2.DateCreated","Iptc.Application2.TimeCreated"),"Exif.Photo.DateTimeOriginal",),False),
("Title","Title",True,conv_str_alt,None,None,None,("Xmp.dc.title","Iptc.Application2.Headline",),True),
("ImageDescription","Image Description",True,conv_str_alt,None,None,None,("Xmp.dc.description","Iptc.Application2.Caption","Exif.Image.ImageDescription",),True),
("Keywords","Tags",True,conv_keywords,tag_bind,tag_split,None,("Xmp.dc.subject","Iptc.Application2.Keywords","Exif.Photo.UserComment"),True),
("Artist","Artist",True,conv_artist,tag_bind_c,tag_split_c,None,("Xmp.dc.creator","Iptc.Application2.Credit","Exif.Image.Artist"),True),
("Copyright","Copyright",True,conv_str_alt,None,None,None,("Xmp.dc.rights","Iptc.Application2.Copyright","Exif.Image.Copyright",),True),
#("Rating",True,conv_int,("Xmp.xmp.Rating")),
("Album","Album",True,conv_str,None,None,None,("Iptc.Application2.Subject",),True),
("Make","Make",False,conv_str,None,None,None,("Exif.Image.Make",),False),
("Model","Model",False,conv_str,None,None,None,("Exif.Image.Model",),False),
("Orientation","Orientation",False,conv_int,str,int,None,("Exif.Image.Orientation",),True),
("ExposureTime","Exposure Time",False,conv_rational,rat2str,str2rat,rational_as_float,("Exif.Photo.ExposureTime",),False),
("FNumber","FNumber",False,conv_rational,rat2str,str2rat,rational_as_float,("Exif.Photo.FNumber",),False),
("IsoSpeed","Iso Speed",False,conv_int,str,int,None,("Exif.Photo.ISOSpeedRatings",),False),
("FocalLength","Focal Length",False,conv_rational,rat2str,str2rat,rational_as_float,("Exif.Photo.FocalLength",),False),
("ExposureProgram","Exposure Program",False,conv_int,None,None,None,("Exif.Photo.ExposureProgram",),False),
("ExposureBiasValue","Exposure Bias Value",False,conv_rational,rat2str,str2rat,rational_as_float,("Exif.Photo.ExposureBiasValue",),False),
("ExposureMode","Exposure Mode",False,conv_int,None,None,None,("Exif.Photo.ExposureMode",),False),
("MeteringMode","Metering Mode",False,conv_int,None,None,None,("Exif.Photo.MeteringMode",),False),
("Flash","Flash",False,conv_int,None,None,None,("Exif.Photo.Flash",),False),
("SensingMethod","Sensing Method",False,conv_int,None,None,None,("Exif.Photo.SensingMethod",),False),
("WhiteBalance","White Balance",False,conv_int,None,None,None,("Exif.Photo.WhiteBalance",),False),
("DigitalZoomRatio","Digital Zoom Ratio",False,conv_rational,None,None,None,("Exif.Photo.DigitalZoomRatio",),False),
("SceneCaptureType","Scene Capture Type",False,conv_int,None,None,None,("Exif.Photo.SceneCaptureType",),False),
("GainControl","Gain Control",False,conv_int,None,None,None,("Exif.Photo.GainControl",),False),
("Contrast","Contrast",False,conv_int,None,None,None,("Exif.Photo.Contrast",),False),
("Saturation","Saturation",False,conv_int,None,None,None,("Exif.Photo.Saturation",),False),
("Sharpness","Sharpness",False,conv_int,None,None,None,("Exif.Photo.Sharpness",),False),
("SubjectDistanceRange","Subject Distance",False,conv_int,None,None,None,("Exif.Photo.SubjectDistanceRange",),False),
("Software","Software",False,conv_str,None,None,None,("Exif.Image.Software",),False),
("IPTCNAA","IPTCNAA",False,conv_str,None,None,None,("Exif.Image.IPTCNAA",),False),
("ImageUniqueID","Image Unique ID",False,conv_str,None,None,None,("Exif.Photo.ImageUniqueID",),False),
("Processing Software","Processing Software",False,conv_str,None,None,None,("Exif.Image.ProcessingSoftware",),False),
("LatLon","Geolocation",False,conv_latlon,tup2str,str2tup,None,("Exif.GPSInfo.GPSLatitude","Exif.GPSInfo.GPSLatitudeRef","Exif.GPSInfo.GPSLongitude","Exif.GPSInfo.GPSLongitudeRef"),False),
("ImageTransforms","Image Transformations",False,conv_image_transforms,json.dumps,json.loads,None,("Xmp.picty.ImageTransformations",),True),
##("GPSTimeStamp","GPSTimeStamp",False,must convert a len 3 tuple of rationals("Exif.GPSInfo.GPSTimeStamp",))
)

apptags_sidecar=(
("DateTaken","Date Taken",False,conv_date_taken_xmp,None,None,date_as_sortable,("Xmp.photoshop.DateCreated","Xmp.xmp.CreateDate"),False),
("Title","Title",True,conv_lang_alt,None,None,None,("Xmp.dc.title",),True),
("ImageDescription","Image Description",True,conv_lang_alt,None,None,None,("Xmp.dc.description",),True),
("Keywords","Tags",True,conv_keywords_xmp,tag_bind,tag_split,None,("Xmp.dc.subject",),True),
("Artist","Artist",True,conv_list_xmp,tag_bind_c,tag_split_c,None,("Xmp.dc.creator",),True),
("Copyright","Copyright",True,conv_lang_alt,None,None,None,("Xmp.dc.rights",),True),
#("Rating",True,conv_int,("Xmp.xmp.Rating")),
("Album","Album",True,conv_list_xmp,tag_bind_c,tag_split_c,None,("Xmp.photoshop.SupplementalCategories",),True),
("Make","Make",False,conv_str,None,None,None,("Xmp.tiff.Make",),False),
("Model","Model",False,conv_str,None,None,None,("Xmp.tiff.Model",),False),
("Orientation","Orientation",False,conv_int,str,int,None,("Xmp.tiff.Orientation",),True),
("ExposureTime","Exposure Time",False,conv_rational,rat2str,str2rat,rational_as_float,("Xmp.exif.ExposureTime",),False),
("FNumber","FNumber",False,conv_rational,rat2str,str2rat,rational_as_float,("Xmp.exif.FNumber",),False),
("IsoSpeed","Iso Speed",False,conv_int,str,int,None,("Xmp.exif.ISOSpeedRatings",),False),
("FocalLength","Focal Length",False,conv_rational,rat2str,str2rat,rational_as_float,("Xmp.exif.FocalLength",),False),
("ExposureProgram","Exposure Program",False,conv_int,None,None,None,("Xmp.exif.ExposureProgram",),False),
("ExposureBiasValue","Exposure Bias Value",False,conv_rational,rat2str,str2rat,rational_as_float,("Xmp.exif.ExposureBiasValue",),False),
("ExposureMode","Exposure Mode",False,conv_int,None,None,None,("Xmp.exif.ExposureMode",),False),
("MeteringMode","Metering Mode",False,conv_int,None,None,None,("Xmp.exif.MeteringMode",),False),
("Flash","Flash",False,conv_int,None,None,None,("Xmp.exif.Flash",),False),
("SensingMethod","Sensing Method",False,conv_int,None,None,None,("Xmp.exif.SensingMethod",),False),
("WhiteBalance","White Balance",False,conv_int,None,None,None,("Xmp.exif.WhiteBalance",),False),
("DigitalZoomRatio","Digital Zoom Ratio",False,conv_rational,None,None,None,("Xmp.exif.DigitalZoomRatio",),False),
("SceneCaptureType","Scene Capture Type",False,conv_int,None,None,None,("Xmp.exif.SceneCaptureType",),False),
("GainControl","Gain Control",False,conv_int,None,None,None,("Xmp.exif.GainControl",),False),
("Contrast","Contrast",False,conv_int,None,None,None,("Xmp.exif.Contrast",),False),
("Saturation","Saturation",False,conv_int,None,None,None,("Xmp.exif.Saturation",),False),
("Sharpness","Sharpness",False,conv_int,None,None,None,("Xmp.exif.Sharpness",),False),
("SubjectDistanceRange","Subject Distance",False,conv_int,None,None,None,("Xmp.exif.SubjectDistanceRange",),False),
("Software","Software",False,conv_str,None,None,None,("Xmp.tiff.Software",),False),
##("IPTCNAA","IPTCNAA",False,conv_str,None,None,None,("Exif.Image.IPTCNAA",),False),
("ImageUniqueID","Image Unique ID",False,conv_str,None,None,None,("Xmp.exif.ImageUniqueID",),False),
##("Processing Software","Processing Software",False,conv_str,None,None,None,("Exif.Image.ProcessingSoftware",),False),
("LatLon","Geolocation",False,conv_latlon,tup2str,str2tup,None,("Xmp.exif.GPSLatitude","Xmp.exif.GPSLatitudeRef","Xmp.exif.GPSLongitude","Xmp.exif.GPSLongitudeRef"),False),
("ImageTransforms","Image Transformations",False,conv_image_transforms,json.dumps,json.loads,None,("Xmp.picty.ImageTransformations",),True),
)


##todo: probably makes sense to just remove this -- was at one time used to define the keys to write in imagemanip.save_metadata
## better (more conservative) to just write only the keys that have changed
##writetags=[(x[0],x[1]) for x in apptags if x[3]]
##writetags.append(('Orientation','Orientation'))

apptags_dict=dict([(x[0],x[1:]) for x in apptags])
appkeys=[y for x in apptags for y in x[7]]

apptags_dict_sidecar=dict([(x[0],x[1:]) for x in apptags_sidecar])
appkeys_sidecar=[y for x in apptags_sidecar for y in x[7]]

def get_exiv2_meta(app_meta,exiv2_meta,apptags_dict=apptags_dict):
    for appkey,data in apptags_dict.iteritems():
        try:
            val=data[2](exiv2_meta,data[6])
            if val:
                app_meta[appkey]=val
        except:
            pass

def set_exiv2_meta(app_meta,exiv2_meta,apptags_dict=apptags_dict):
    i=0
    for appkey in apptags_dict:
        try:
            data=apptags_dict[appkey] ##TODO: Check that keys are being removed if the app_meta value is equivalent to empty
            if appkey in app_meta:
                if data[2](exiv2_meta,data[6]) != app_meta[appkey]:
                    i+=1
                    data[2](exiv2_meta,data[6],app_meta[appkey])
            elif data[7] == True:
                for exiv2_key in data[6]:
                    try:
                        del exiv2_meta[exiv2_key]
                    except KeyError:
                        pass
        except:
            print 'Error setting Exiv2 key',appkey
            import traceback,sys
            print traceback.format_exc(sys.exc_info()[2])

def app_key_from_string(key,string):
    fn=apptags_dict[key][4]
    if fn:
        try:
            return fn(string)
        except:
            return None
    else:
        return string

def app_key_to_string(key,value):
    try:
        return apptags_dict[key][3](value)
    except:
        try:
            return str(value)
        except:
            return None

def app_key_as_sortable(app_meta,key):
    if apptags_dict[key][5]!=None:
        try:
            return apptags_dict[key][5](app_meta[key])
        except:
            return None
    else:
        try:
            return app_meta[key]
        except:
            return None





'''
all_tags=(("Exif.Image.ProcessingSoftware","Ascii","The name and version of the software used to post-process the picture."),
("Exif.Image.NewSubfileType","Long","A general indication of the kind of data contained in this subfile."),
("Exif.Image.ImageWidth","Long","The number of columns of image data, equal to the number of pixels per row. In JPEG compressed data a JPEG marker is used instead of this tag."),
("Exif.Image.ImageLength","Long","The number of rows of image data. In JPEG compressed data a JPEG marker is used instead of this tag."),
("Exif.Image.BitsPerSample","Short","The number of bits per image component. In this standard each component of the image is 8 bits, so the value for this tag is 8. See also <SamplesPerPixel>. In JPEG compressed data a JPEG marker is used instead of this tag."),
("Exif.Image.Compression","Short","The compression scheme used for the image data. When a primary image is JPEG compressed, this designation is not necessary and is omitted. When thumbnails use JPEG compression, this tag value is set to 6."),
("Exif.Image.PhotometricInterpretation","Short","The pixel composition. In JPEG compressed data a JPEG marker is used instead of this tag."),
("Exif.Image.FillOrder","Short","The logical order of bits within a byte"),
("Exif.Image.DocumentName","Ascii","The name of the document from which this image was scanned"),
("Exif.Image.ImageDescription","Ascii","A character string giving the title of the image. It may be a comment such as '1988 company picnic' or the like. Two-bytes character codes cannot be used. When a 2-bytes code is necessary, the Exif Private tag <UserComment> is to be used."),
("Exif.Image.Make","Ascii","The manufacturer of the recording equipment. This is the manufacturer of the DSC, scanner, video digitizer or other equipment that generated the image. When the field is left blank, it is treated as unknown."),
("Exif.Image.Model","Ascii","The model name or model number of the equipment. This is the model name or number of the DSC, scanner, video digitizer or other equipment that generated the image. When the field is left blank, it is treated as unknown."),
("Exif.Image.StripOffsets","Long","For each strip, the byte offset of that strip. It is recommended that this be selected so the number of strip bytes does not exceed 64 Kbytes. With JPEG compressed data this designation is not needed and is omitted. See also <RowsPerStrip> and <StripByteCounts>."),
("Exif.Image.Orientation","Short","The image orientation viewed in terms of rows and columns."),
("Exif.Image.SamplesPerPixel","Short","The number of components per pixel. Since this standard applies to RGB and YCbCr images, the value set for this tag is 3. In JPEG compressed data a JPEG marker is used instead of this tag."),
("Exif.Image.RowsPerStrip","Long","The number of rows per strip. This is the number of rows in the image of one strip when an image is divided into strips. With JPEG compressed data this designation is not needed and is omitted. See also <StripOffsets> and <StripByteCounts>."),
("Exif.Image.StripByteCounts","Long","The total number of bytes in each strip. With JPEG compressed data this designation is not needed and is omitted."),
("Exif.Image.XResolution","Rational","The number of pixels per <ResolutionUnit> in the <ImageWidth> direction. When the image resolution is unknown, 72 [dpi] is designated."),
("Exif.Image.YResolution","Rational","The number of pixels per <ResolutionUnit> in the <ImageLength> direction. The same value as <XResolution> is designated."),
("Exif.Image.PlanarConfiguration","Short","Indicates whether pixel components are recorded in a chunky or planar format. In JPEG compressed files a JPEG marker is used instead of this tag. If this field does not exist, the TIFF default of 1 (chunky) is assumed."),
("Exif.Image.ResolutionUnit","Short","The unit for measuring <XResolution> and <YResolution>. The same unit is used for both <XResolution> and <YResolution>. If the image resolution is unknown, 2 (inches) is designated."),
("Exif.Image.TransferFunction","Short","A transfer function for the image, described in tabular style. Normally this tag is not necessary, since color space is specified in the color space information tag (<ColorSpace>)."),
("Exif.Image.Software","Ascii","This tag records the name and version of the software or firmware of the camera or image input device used to generate the image. The detailed format is not specified, but it is recommended that the example shown below be followed. When the field is left blank, it is treated as unknown."),
("Exif.Image.DateTime","Ascii","The date and time of image creation. In Exif standard, it is the date and time the file was changed."),
("Exif.Image.HostComputer","Ascii","This tag records information about the host computer used to generate the image."),
("Exif.Image.Artist","Ascii","This tag records the name of the camera owner, photographer or image creator. The detailed format is not specified, but it is recommended that the information be written as in the example below for ease of Interoperability. When the field is left blank, it is treated as unknown. Ex.) 'Camera owner, John Smith; Photographer, Michael Brown; Image creator, Ken James'"),
("Exif.Image.WhitePoint","Rational","The chromaticity of the white point of the image. Normally this tag is not necessary, since color space is specified in the colorspace information tag (<ColorSpace>)."),
("Exif.Image.PrimaryChromaticities","Rational","The chromaticity of the three primary colors of the image. Normally this tag is not necessary, since colorspace is specified in the colorspace information tag (<ColorSpace>)."),
("Exif.Image.TileWidth","Short","The tile width in pixels. This is the number of columns in each tile."),
("Exif.Image.TileLength","Short","The tile length (height) in pixels. This is the number of rows in each tile."),
("Exif.Image.TileOffsets","Short","For each tile, the byte offset of that tile, as compressed and stored on disk. The offset is specified with respect to the beginning of the TIFF file. Note that this implies that each tile has a location independent of the locations of other tiles."),
("Exif.Image.TileByteCounts","Short","For each tile, the number of (compressed) bytes in that tile. See TileOffsets for a description of how the byte counts are ordered."),
("Exif.Image.SubIFDs","Long","Defined by Adobe Corporation to enable TIFF Trees within a TIFF file."),
("Exif.Image.TransferRange","Short","Expands the range of the TransferFunction"),
("Exif.Image.JPEGProc","Long","This field indicates the process used to produce the compressed data"),
("Exif.Image.JPEGInterchangeFormat","Long","The offset to the start byte (SOI) of JPEG compressed thumbnail data. This is not used for primary image JPEG data."),
("Exif.Image.JPEGInterchangeFormatLength","Long","The number of bytes of JPEG compressed thumbnail data. This is not used for primary image JPEG data. JPEG thumbnails are not divided but are recorded as a continuous JPEG bitstream from SOI to EOI. Appn and COM markers should not be recorded. Compressed thumbnails must be recorded in no more than 64 Kbytes, including all other data to be recorded in APP1."),
("Exif.Image.YCbCrCoefficients","Rational","The matrix coefficients for transformation from RGB to YCbCr image data. No default is given in TIFF; but here the value given in Appendix E, 'Color Space Guidelines', is used as the default. The color space is declared in a color space information tag, with the default being the value that gives the optimal image characteristics Interoperability this condition."),
("Exif.Image.YCbCrSubSampling","Short","The sampling ratio of chrominance components in relation to the luminance component. In JPEG compressed data a JPEG marker is used instead of this tag."),
("Exif.Image.YCbCrPositioning","Short","The position of chrominance components in relation to the luminance component. This field is designated only for JPEG compressed data or uncompressed YCbCr data. The TIFF default is 1 (centered); but when Y:Cb:Cr = 4:2:2 it is recommended in this standard that 2 (co-sited) be used to record data, in order to improve the image quality when viewed on TV systems. When this field does not exist, the reader shall assume the TIFF default. In the case of Y:Cb:Cr = 4:2:0, the TIFF default (centered) is recommended. If the reader does not have the capability of supporting both kinds of <YCbCrPositioning>, it shall follow the TIFF default regardless of the value in this field. It is preferable that readers be able to support both centered and co-sited positioning."),
("Exif.Image.ReferenceBlackWhite","Rational","The reference black point value and reference white point value. No defaults are given in TIFF, but the values below are given as defaults here. The color space is declared in a color space information tag, with the default being the value that gives the optimal image characteristics Interoperability these conditions."),
("Exif.Image.XMLPacket","Byte","XMP Metadata (Adobe technote 9-14-02)"),
("Exif.Image.Rating","Short","Rating tag used by Windows"),
("Exif.Image.RatingPercent","Short","Rating tag used by Windows, value in percent"),
("Exif.Image.CFARepeatPatternDim","Short","Contains two values representing the minimum rows and columns to define the repeating patterns of the color filter array"),
("Exif.Image.CFAPattern","Byte","Indicates the color filter array (CFA) geometric pattern of the image sensor when a one-chip color area sensor is used. It does not apply to all sensing methods"),
("Exif.Image.BatteryLevel","Rational","Contains a value of the battery level as a fraction or string"),
("Exif.Image.IPTCNAA","Long","Contains an IPTC/NAA record"),
("Exif.Image.Copyright","Ascii","Copyright information. In this standard the tag is used to indicate both the photographer and editor copyrights. It is the copyright notice of the person or organization claiming rights to the image. The Interoperability copyright statement including date and rights should be written in this field; e.g., 'Copyright, John Smith, 19xx. All rights reserved.'. In this standard the field records both the photographer and editor copyrights, with each recorded in a separate part of the statement. When there is a clear distinction between the photographer and editor copyrights, these are to be written in the order of photographer followed by editor copyright, separated by NULL (in this case since the statement also ends with a NULL, there are two NULL codes). When only the photographer copyright is given, it is terminated by one NULL code . When only the editor copyright is given, the photographer copyright part consists of one space followed by a terminating NULL code, then the editor copyright is given. When the field is left blank, it is treated as unknown."),
("Exif.Image.ImageResources","Undefined","Contains information embedded by the Adobe Photoshop application"),
("Exif.Image.ExifTag","Long","A pointer to the Exif IFD. Interoperability, Exif IFD has the same structure as that of the IFD specified in TIFF. ordinarily, however, it does not contain image data as in the case of TIFF."),
("Exif.Image.InterColorProfile","Undefined","Contains an InterColor Consortium (ICC) format color space characterization/profile"),
("Exif.Image.GPSTag","Long","A pointer to the GPS Info IFD. The Interoperability structure of the GPS Info IFD, like that of Exif IFD, has no image data."),
("Exif.Image.TIFFEPStandardID","Byte","Contains four ASCII characters representing the TIFF/EP standard version of a TIFF/EP file, eg '1', '0', '0', '0'"),
("Exif.Image.XPTitle","Byte","Title tag used by Windows, encoded in UCS2"),
("Exif.Image.XPComment","Byte","Comment tag used by Windows, encoded in UCS2"),
("Exif.Image.XPAuthor","Byte","Author tag used by Windows, encoded in UCS2"),
("Exif.Image.XPKeywords","Byte","Keywords tag used by Windows, encoded in UCS2"),
("Exif.Image.XPSubject","Byte","Subject tag used by Windows, encoded in UCS2"),
("Exif.Image.PrintImageMatching","Undefined","Print Image Matching, descriptiont needed."),
("Exif.Image.DNGVersion","Byte","This tag encodes the DNG four-tier version number. For files compliant with version 1.1.0.0 of the DNG specification, this tag should contain the bytes: 1, 1, 0, 0."),
("Exif.Image.DNGBackwardVersion","Byte","This tag specifies the oldest version of the Digital Negative specification for which a file is compatible. Readers shouldnot attempt to read a file if this tag specifies a version number that is higher than the version number of the specification the reader was based on. In addition to checking the version tags, readers should, for all tags, check the types, counts, and values, to verify it is able to correctly read the file."),
("Exif.Image.UniqueCameraModel","Ascii","Defines a unique, non-localized name for the camera model that created the image in the raw file. This name should include the manufacturer's name to avoid conflicts, and should not be localized, even if the camera name itself is localized for different markets (see LocalizedCameraModel). This string may be used by reader software to index into per-model preferences and replacement profiles."),
("Exif.Image.LocalizedCameraModel","Byte","Similar to the UniqueCameraModel field, except the name can be localized for different markets to match the localization of the camera name."),
("Exif.Image.CFAPlaneColor","Byte","Provides a mapping between the values in the CFAPattern tag and the plane numbers in LinearRaw space. This is a required tag for non-RGB CFA images."),
("Exif.Image.CFALayout","Short","Describes the spatial layout of the CFA."),
("Exif.Image.LinearizationTable","Short","Describes a lookup table that maps stored values into linear values. This tag is typically used to increase compression ratios by storing the raw data in a non-linear, more visually uniform space with fewer total encoding levels. If SamplesPerPixel is not equal to one, this single table applies to all the samples for each pixel."),
("Exif.Image.BlackLevelRepeatDim","Short","Specifies repeat pattern size for the BlackLevel tag."),
("Exif.Image.BlackLevel","Rational","Specifies the zero light (a.k.a. thermal black or black current) encoding level, as a repeating pattern. The origin of this pattern is the top-left corner of the ActiveArea rectangle. The values are stored in row-column-sample scan order."),
("Exif.Image.BlackLevelDeltaH","SRational","If the zero light encoding level is a function of the image column, BlackLevelDeltaH specifies the difference between the zero light encoding level for each column and the baseline zero light encoding level. If SamplesPerPixel is not equal to one, this single table applies to all the samples for each pixel."),
("Exif.Image.BlackLevelDeltaV","SRational","If the zero light encoding level is a function of the image row, this tag specifies the difference between the zero light encoding level for each row and the baseline zero light encoding level. If SamplesPerPixel is not equal to one, this single table applies to all the samples for each pixel."),
("Exif.Image.WhiteLevel","Short","This tag specifies the fully saturated encoding level for the raw sample values. Saturation is caused either by the sensor itself becoming highly non-linear in response, or by the camera's analog to digital converter clipping."),
("Exif.Image.DefaultScale","Rational","DefaultScale is required for cameras with non-square pixels. It specifies the default scale factors for each direction to convert the image to square pixels. Typically these factors are selected to approximately preserve total pixel count. For CFA images that use CFALayout equal to 2, 3, 4, or 5, such as the Fujifilm SuperCCD, these two values should usually differ by a factor of 2.0."),
("Exif.Image.DefaultCropOrigin","Short","Raw images often store extra pixels around the edges of the final image. These extra pixels help prevent interpolation artifacts near the edges of the final image. DefaultCropOrigin specifies the origin of the final image area, in raw image coordinates (i.e., before the DefaultScale has been applied), relative to the top-left corner of the ActiveArea rectangle."),
("Exif.Image.DefaultCropSize","Short","Raw images often store extra pixels around the edges of the final image. These extra pixels help prevent interpolation artifacts near the edges of the final image. DefaultCropSize specifies the size of the final image area, in raw image coordinates (i.e., before the DefaultScale has been applied)."),
("Exif.Image.ColorMatrix1","SRational","ColorMatrix1 defines a transformation matrix that converts XYZ values to reference camera native color space values, under the first calibration illuminant. The matrix values are stored in row scan order. The ColorMatrix1 tag is required for all non-monochrome DNG files."),
("Exif.Image.ColorMatrix2","SRational","ColorMatrix2 defines a transformation matrix that converts XYZ values to reference camera native color space values, under the second calibration illuminant. The matrix values are stored in row scan order."),
("Exif.Image.CameraCalibration1","SRational","CameraClalibration1 defines a calibration matrix that transforms reference camera native space values to individual camera native space values under the first calibration illuminant. The matrix is stored in row scan order. This matrix is stored separately from the matrix specified by the ColorMatrix1 tag to allow raw converters to swap in replacement color matrices based on UniqueCameraModel tag, while still taking advantage of any per-individual camera calibration performed by the camera manufacturer."),
("Exif.Image.CameraCalibration2","SRational","CameraCalibration2 defines a calibration matrix that transforms reference camera native space values to individual camera native space values under the second calibration illuminant. The matrix is stored in row scan order. This matrix is stored separately from the matrix specified by the ColorMatrix2 tag to allow raw converters to swap in replacement color matrices based on UniqueCameraModel tag, while still taking advantage of any per-individual camera calibration performed by the camera manufacturer."),
("Exif.Image.ReductionMatrix1","SRational","ReductionMatrix1 defines a dimensionality reduction matrix for use as the first stage in converting color camera native space values to XYZ values, under the first calibration illuminant. This tag may only be used if ColorPlanes is greater than 3. The matrix is stored in row scan order."),
("Exif.Image.ReductionMatrix2","SRational","ReductionMatrix2 defines a dimensionality reduction matrix for use as the first stage in converting color camera native space values to XYZ values, under the second calibration illuminant. This tag may only be used if ColorPlanes is greater than 3. The matrix is stored in row scan order."),
("Exif.Image.AnalogBalance","Rational","Normally the stored raw values are not white balanced, since any digital white balancing will reduce the dynamic range of the final image if the user decides to later adjust the white balance; however, if camera hardware is capable of white balancing the color channels before the signal is digitized, it can improve the dynamic range of the final image. AnalogBalance defines the gain, either analog (recommended) or digital (not recommended) that has been applied the stored raw values."),
("Exif.Image.AsShotNeutral","Short","Specifies the selected white balance at time of capture, encoded as the coordinates of a perfectly neutral color in linear reference space values. The inclusion of this tag precludes the inclusion of the AsShotWhiteXY tag."),
("Exif.Image.AsShotWhiteXY","Rational","Specifies the selected white balance at time of capture, encoded as x-y chromaticity coordinates. The inclusion of this tag precludes the inclusion of the AsShotNeutral tag."),
("Exif.Image.BaselineExposure","SRational","Camera models vary in the trade-off they make between highlight headroom and shadow noise. Some leave a significant amount of highlight headroom during a normal exposure. This allows significant negative exposure compensation to be applied during raw conversion, but also means normal exposures will contain more shadow noise. Other models leave less headroom during normal exposures. This allows for less negative exposure compensation, but results in lower shadow noise for normal exposures. Because of these differences, a raw converter needs to vary the zero point of its exposure compensation control from model to model. BaselineExposure specifies by how much (in EV units) to move the zero point. Positive values result in brighter default results, while negative values result in darker default results."),
("Exif.Image.BaselineNoise","Rational","Specifies the relative noise level of the camera model at a baseline ISO value of 100, compared to a reference camera model. Since noise levels tend to vary approximately with the square root of the ISO value, a raw converter can use this value, combined with the current ISO, to estimate the relative noise level of the current image."),
("Exif.Image.BaselineSharpness","Rational","Specifies the relative amount of sharpening required for this camera model, compared to a reference camera model. Camera models vary in the strengths of their anti-aliasing filters. Cameras with weak or no filters require less sharpening than cameras with strong anti-aliasing filters."),
("Exif.Image.BayerGreenSplit","Long","Only applies to CFA images using a Bayer pattern filter array. This tag specifies, in arbitrary units, how closely the values of the green pixels in the blue/green rows track the values of the green pixels in the red/green rows. A value of zero means the two kinds of green pixels track closely, while a non-zero value means they sometimes diverge. The useful range for this tag is from 0 (no divergence) to about 5000 (quite large divergence)."),
("Exif.Image.LinearResponseLimit","Rational","Some sensors have an unpredictable non-linearity in their response as they near the upper limit of their encoding range. This non-linearity results in color shifts in the highlight areas of the resulting image unless the raw converter compensates for this effect. LinearResponseLimit specifies the fraction of the encoding range above which the response may become significantly non-linear."),
("Exif.Image.CameraSerialNumber","Ascii","CameraSerialNumber contains the serial number of the camera or camera body that captured the image."),
("Exif.Image.LensInfo","Rational","Contains information about the lens that captured the image. If the minimum f-stops are unknown, they should be encoded as 0/0."),
("Exif.Image.ChromaBlurRadius","Rational","ChromaBlurRadius provides a hint to the DNG reader about how much chroma blur should be applied to the image. If this tag is omitted, the reader will use its default amount of chroma blurring. Normally this tag is only included for non-CFA images, since the amount of chroma blur required for mosaic images is highly dependent on the de-mosaic algorithm, in which case the DNG reader's default value is likely optimized for its particular de-mosaic algorithm."),
("Exif.Image.AntiAliasStrength","Rational","Provides a hint to the DNG reader about how strong the camera's anti-alias filter is. A value of 0.0 means no anti-alias filter (i.e., the camera is prone to aliasing artifacts with some subjects), while a value of 1.0 means a strong anti-alias filter (i.e., the camera almost never has aliasing artifacts)."),
("Exif.Image.ShadowScale","SRational","This tag is used by Adobe Camera Raw to control the sensitivity of its 'Shadows' slider."),
("Exif.Image.DNGPrivateData","Byte","Provides a way for camera manufacturers to store private data in the DNG file for use by their own raw converters, and to have that data preserved by programs that edit DNG files."),
("Exif.Image.MakerNoteSafety","Short","MakerNoteSafety lets the DNG reader know whether the EXIF MakerNote tag is safe to preserve along with the rest of the EXIF data. File browsers and other image management software processing an image with a preserved MakerNote should be aware that any thumbnail image embedded in the MakerNote may be stale, and may not reflect the current state of the full size image."),
("Exif.Image.CalibrationIlluminant1","Short","The illuminant used for the first set of color calibration tags (ColorMatrix1, CameraCalibration1, ReductionMatrix1). The legal values for this tag are the same as the legal values for the LightSource EXIF tag."),
("Exif.Image.CalibrationIlluminant2","Short","The illuminant used for an optional second set of color calibration tags (ColorMatrix2, CameraCalibration2, ReductionMatrix2). The legal values for this tag are the same as the legal values for the CalibrationIlluminant1 tag; however, if both are included, neither is allowed to have a value of 0 (unknown)."),
("Exif.Image.BestQualityScale","Rational","For some cameras, the best possible image quality is not achieved by preserving the total pixel count during conversion. For example, Fujifilm SuperCCD images have maximum detail when their total pixel count is doubled. This tag specifies the amount by which the values of the DefaultScale tag need to be multiplied to achieve the best quality image size."),
("Exif.Image.RawDataUniqueID","Byte","This tag contains a 16-byte unique identifier for the raw image data in the DNG file. DNG readers can use this tag to recognize a particular raw image, even if the file's name or the metadata contained in the file has been changed. If a DNG writer creates such an identifier, it should do so using an algorithm that will ensure that it is very unlikely two different images will end up having the same identifier."),
("Exif.Image.OriginalRawFileName","Byte","If the DNG file was converted from a non-DNG raw file, then this tag contains the file name of that original raw file."),
("Exif.Image.OriginalRawFileData","Undefined","If the DNG file was converted from a non-DNG raw file, then this tag contains the compressed contents of that original raw file. The contents of this tag always use the big-endian byte order. The tag contains a sequence of data blocks. Future versions of the DNG specification may define additional data blocks, so DNG readers should ignore extra bytes when parsing this tag. DNG readers should also detect the case where data blocks are missing from the end of the sequence, and should assume a default value for all the missing blocks. There are no padding or alignment bytes between data blocks."),
("Exif.Image.ActiveArea","Short","This rectangle defines the active (non-masked) pixels of the sensor. The order of the rectangle coordinates is: top, left, bottom, right."),
("Exif.Image.MaskedAreas","Short","This tag contains a list of non-overlapping rectangle coordinates of fully masked pixels, which can be optionally used by DNG readers to measure the black encoding level. The order of each rectangle's coordinates is: top, left, bottom, right. If the raw image data has already had its black encoding level subtracted, then this tag should not be used, since the masked pixels are no longer useful."),
("Exif.Image.AsShotICCProfile","Undefined","This tag contains an ICC profile that, in conjunction with the AsShotPreProfileMatrix tag, provides the camera manufacturer with a way to specify a default color rendering from camera color space coordinates (linear reference values) into the ICC profile connection space. The ICC profile connection space is an output referred colorimetric space, whereas the other color calibration tags in DNG specify a conversion into a scene referred colorimetric space. This means that the rendering in this profile should include any desired tone and gamut mapping needed to convert between scene referred values and output referred values."),
("Exif.Image.AsShotPreProfileMatrix","SRational","This tag is used in conjunction with the AsShotICCProfile tag. It specifies a matrix that should be applied to the camera color space coordinates before processing the values through the ICC profile specified in the AsShotICCProfile tag. The matrix is stored in the row scan order. If ColorPlanes is greater than three, then this matrix can (but is not required to) reduce the dimensionality of the color data down to three components, in which case the AsShotICCProfile should have three rather than ColorPlanes input components."),
("Exif.Image.CurrentICCProfile","Undefined","This tag is used in conjunction with the CurrentPreProfileMatrix tag. The CurrentICCProfile and CurrentPreProfileMatrix tags have the same purpose and usage as the AsShotICCProfile and AsShotPreProfileMatrix tag pair, except they are for use by raw file editors rather than camera manufacturers."),
("Exif.Image.CurrentPreProfileMatrix","SRational","This tag is used in conjunction with the CurrentICCProfile tag. The CurrentICCProfile and CurrentPreProfileMatrix tags have the same purpose and usage as the AsShotICCProfile and AsShotPreProfileMatrix tag pair, except they are for use by raw file editors rather than camera manufacturers."),
("Exif","Exif.Photo.ExposureTime","Rational,Exposure time, given in seconds (sec)."),
("Exif","Exif.Photo.FNumber","Rational,The F number."),
("Exif","Exif.Photo.ExposureProgram","Short,The class of the program used by the camera to set exposure when the picture is taken."),
("Exif","Exif.Photo.SpectralSensitivity","Ascii,Indicates the spectral sensitivity of each channel of the camera used. The tag value is an ASCII string compatible with the standard developed by the ASTM Technical Committee."),
("Exif","Exif.Photo.ISOSpeedRatings","Short,Indicates the ISO Speed and ISO Latitude of the camera or input device as specified in ISO 12232."),
("Exif","Exif.Photo.OECF","Undefined,Indicates the Opto-Electoric Conversion Function (OECF) specified in ISO 14524. <OECF> is the relationship between the camera optical input and the image values."),
("Exif","Exif.Photo.ExifVersion","Undefined,The version of this standard supported. Nonexistence of this field is taken to mean nonconformance to the standard."),
("Exif","Exif.Photo.DateTimeOriginal","Ascii,The date and time when the original image data was generated. For a digital still camera the date and time the picture was taken are recorded."),
("Exif","Exif.Photo.DateTimeDigitized","Ascii,The date and time when the image was stored as digital data."),
("Exif","Exif.Photo.ComponentsConfiguration","Undefined,Information specific to compressed data. The channels of each component are arranged in order from the 1st component to the 4th. For uncompressed data the data arrangement is given in the <PhotometricInterpretation> tag. However, since <PhotometricInterpretation> can only express the order of Y, Cb and Cr, this tag is provided for cases when compressed data uses components other than Y, Cb, and Cr and to enable support of other sequences."),
("Exif","Exif.Photo.CompressedBitsPerPixel","Rational,Information specific to compressed data. The compression mode used for a compressed image is indicated in unit bits per pixel."),
("Exif","Exif.Photo.ShutterSpeedValue","SRational,Shutter speed. The unit is the APEX (Additive System of Photographic Exposure) setting."),
("Exif","Exif.Photo.ApertureValue","Rational,The lens aperture. The unit is the APEX value."),
("Exif","Exif.Photo.BrightnessValue","SRational,The value of brightness. The unit is the APEX value. Ordinarily it is given in the range of -99.99 to 99.99."),
("Exif","Exif.Photo.ExposureBiasValue","SRational,The exposure bias. The units is the APEX value. Ordinarily it is given in the range of -99.99 to 99.99."),
("Exif","Exif.Photo.MaxApertureValue","Rational,The smallest F number of the lens. The unit is the APEX value. Ordinarily it is given in the range of 00.00 to 99.99, but it is not limited to this range."),
("Exif","Exif.Photo.SubjectDistance","Rational,The distance to the subject, given in meters."),
("Exif","Exif.Photo.MeteringMode","Short,The metering mode."),
("Exif","Exif.Photo.LightSource","Short,The kind of light source."),
("Exif","Exif.Photo.Flash","Short,This tag is recorded when an image is taken using a strobe light (flash)."),
("Exif","Exif.Photo.FocalLength","Rational,The actual focal length of the lens, in mm. Conversion is not made to the focal length of a 35 mm film camera."),
("Exif","Exif.Photo.SubjectArea","Short,This tag indicates the location and area of the main subject in the overall scene."),
("Exif","Exif.Photo.MakerNote","Undefined,A tag for manufacturers of Exif writers to record any desired information. The contents are up to the manufacturer."),
("Exif","Exif.Photo.UserComment","Comment,A tag for Exif users to write keywords or comments on the image besides those in <ImageDescription>, and without the character code limitations of the <ImageDescription> tag."),
("Exif","Exif.Photo.SubSecTime","Ascii,A tag used to record fractions of seconds for the <DateTime> tag."),
("Exif","Exif.Photo.SubSecTimeOriginal","Ascii,A tag used to record fractions of seconds for the <DateTimeOriginal> tag."),
("Exif","Exif.Photo.SubSecTimeDigitized","Ascii,A tag used to record fractions of seconds for the <DateTimeDigitized> tag."),
("Exif","Exif.Photo.FlashpixVersion","Undefined,The FlashPix format version supported by a FPXR file."),
("Exif","Exif.Photo.ColorSpace","Short,The color space information tag is always recorded as the color space specifier. Normally sRGB is used to define the color space based on the PC monitor conditions and environment. If a color space other than sRGB is used, Uncalibrated is set. Image data recorded as Uncalibrated can be treated as sRGB when it is converted to FlashPix."),
("Exif","Exif.Photo.PixelXDimension","Long,Information specific to compressed data. When a compressed file is recorded, the valid width of the meaningful image must be recorded in this tag, whether or not there is padding data or a restart marker. This tag should not exist in an uncompressed file."),
("Exif","Exif.Photo.PixelYDimension","Long,Information specific to compressed data. When a compressed file is recorded, the valid height of the meaningful image must be recorded in this tag, whether or not there is padding data or a restart marker. This tag should not exist in an uncompressed file. Since data padding is unnecessary in the vertical direction, the number of lines recorded in this valid image height tag will in fact be the same as that recorded in the SOF."),
("Exif","Exif.Photo.RelatedSoundFile","Ascii,This tag is used to record the name of an audio file related to the image data. The only relational information recorded here is the Exif audio file name and extension (an ASCII string consisting of 8 characters + '.' + 3 characters). The path is not recorded."),
("Exif","Exif.Photo.InteroperabilityTag","Long,Interoperability IFD is composed of tags which stores the information to ensure the Interoperability and pointed by the following tag located in Exif IFD. The Interoperability structure of Interoperability IFD is the same as TIFF defined IFD structure but does not contain the image data characteristically compared with normal TIFF IFD."),
("Exif","Exif.Photo.FlashEnergy","Rational,Indicates the strobe energy at the time the image is captured, as measured in Beam Candle Power Seconds (BCPS)."),
("Exif","Exif.Photo.SpatialFrequencyResponse","Undefined,This tag records the camera or input device spatial frequency table and SFR values in the direction of image width, image height, and diagonal direction, as specified in ISO 12233."),
("Exif","Exif.Photo.FocalPlaneXResolution","Rational,Indicates the number of pixels in the image width (X) direction per <FocalPlaneResolutionUnit> on the camera focal plane."),
("Exif","Exif.Photo.FocalPlaneYResolution","Rational,Indicates the number of pixels in the image height (V) direction per <FocalPlaneResolutionUnit> on the camera focal plane."),
("Exif","Exif.Photo.FocalPlaneResolutionUnit","Short,Indicates the unit for measuring <FocalPlaneXResolution> and <FocalPlaneYResolution>. This value is the same as the <ResolutionUnit>."),
("Exif","Exif.Photo.SubjectLocation","Short,Indicates the location of the main subject in the scene. The value of this tag represents the pixel at the center of the main subject relative to the left edge, prior to rotation processing as per the <Rotation> tag. The first value indicates the X column number and second indicates the Y row number."),
("Exif","Exif.Photo.ExposureIndex","Rational,Indicates the exposure index selected on the camera or input device at the time the image is captured."),
("Exif","Exif.Photo.SensingMethod","Short,Indicates the image sensor type on the camera or input device."),
("Exif","Exif.Photo.FileSource","Undefined,Indicates the image source. If a DSC recorded the image, this tag value of this tag always be set to 3, indicating that the image was recorded on a DSC."),
("Exif","Exif.Photo.SceneType","Undefined,Indicates the type of scene. If a DSC recorded the image, this tag value must always be set to 1, indicating that the image was directly photographed."),
("Exif","Exif.Photo.CFAPattern","Undefined,Indicates the color filter array (CFA) geometric pattern of the image sensor when a one-chip color area sensor is used. It does not apply to all sensing methods."),
("Exif","Exif.Photo.CustomRendered","Short,This tag indicates the use of special processing on image data, such as rendering geared to output. When special processing is performed, the reader is expected to disable or minimize any further processing."),
("Exif","Exif.Photo.ExposureMode","Short,This tag indicates the exposure mode set when the image was shot. In auto-bracketing mode, the camera shoots a series of frames of the same scene at different exposure settings."),
("Exif","Exif.Photo.WhiteBalance","Short,This tag indicates the white balance mode set when the image was shot."),
("Exif","Exif.Photo.DigitalZoomRatio","Rational,This tag indicates the digital zoom ratio when the image was shot. If the numerator of the recorded value is 0, this indicates that digital zoom was not used."),
("Exif","Exif.Photo.FocalLengthIn35mmFilm","Short,This tag indicates the equivalent focal length assuming a 35mm film camera, in mm. A value of 0 means the focal length is unknown. Note that this tag differs from the <FocalLength> tag."),
("Exif","Exif.Photo.SceneCaptureType","Short,This tag indicates the type of scene that was shot. It can also be used to record the mode in which the image was shot. Note that this differs from the <SceneType> tag."),
("Exif","Exif.Photo.GainControl","Short,This tag indicates the degree of overall image gain adjustment."),
("Exif","Exif.Photo.Contrast","Short,This tag indicates the direction of contrast processing applied by the camera when the image was shot."),
("Exif","Exif.Photo.Saturation","Short,This tag indicates the direction of saturation processing applied by the camera when the image was shot."),
("Exif","Exif.Photo.Sharpness","Short,This tag indicates the direction of sharpness processing applied by the camera when the image was shot."),
("Exif","Exif.Photo.DeviceSettingDescription","Undefined,This tag indicates information on the picture-taking conditions of a particular camera model. The tag is used only to indicate the picture-taking conditions in the reader."),
("Exif","Exif.Photo.SubjectDistanceRange","Short,This tag indicates the distance to the subject."),
("Exif","Exif.Photo.ImageUniqueID","Ascii,This tag indicates an identifier assigned uniquely to each image. It is recorded as an ASCII string equivalent to hexadecimal notation and 128-bit fixed length."),
("Exif.Iop.InteroperabilityIndex","Ascii","Indicates the identification of the Interoperability rule. Use "R98" for stating ExifR98 Rules. Four bytes used including the termination code (NULL). see the separate volume of Recommended Exif Interoperability Rules (ExifR98) for other tags used for ExifR98."),
("Exif.Iop.InteroperabilityVersion","Undefined","Interoperability version"),
("Exif.Iop.RelatedImageFileFormat","Ascii","File format of image file"),
("Exif.Iop.RelatedImageWidth","Long","Image width"),
("Exif.Iop.RelatedImageLength","Long","Image height"))
'''
