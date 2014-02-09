#!/usr/bin/python
#
# File:   moodle2edx.py
# Date:   19-July-2012
# Author: I. Chuang <ichuang@mit.edu>
#
# script to convert a moodle class into an edX course
#
# Usage: python moodle2edx.py [ backup-file.mbz | backup-directory ]

import os, sys, string, re
import codecs
import tempfile
from lxml import etree
from abox import AnswerBox
from path import path
#import xml.sax.saxutils as saxutils
import cgi
import html2text

html2text.IGNORE_EMPHASIS=True

#-----------------------------------------------------------------------------

class Moodle2Edx(object):
    '''
    Python class for converting moodle backup files to edX XML course format
    '''

    def __init__(self, infn, edxdir='.', org="MITx", semester="2014_Spring", verbose=False):
        if infn.endswith('.mbz'):
            # import gzip, tarfile
            # dir = tarfile.TarFile(fileobj=gzip.open(infn))
            mdir = tempfile.mkdtemp(prefix="moodle2edx")
            curdir = os.path.abspath('.')
            os.chdir(mdir)
            os.system('tar xzf %s/%s' % (curdir, infn))
            os.chdir(curdir)
        else:
            mdir = infn
    
        if not os.path.isdir(mdir):
            print "Input argument should be directory name or moodle *.mbz backup file"
            sys.exit(0)
    
        self.verbose = verbose
        self.edxdir = path(edxdir)
        self.moodle_dir = path(mdir)
        if not self.edxdir.exists():
            os.mkdir(self.edxdir)
        def mkdir(mdir):
            if not os.path.exists('%s/%s' % (self.edxdir, mdir)):
                os.mkdir(self.edxdir / mdir)
        edirs = ['html', 'problem', 'course', 'static']
        for ed in edirs:
            mkdir(ed)

        self.URLNAMES = []

        mfn = 'moodle_backup.xml'	# top-level moodle backup xml
        qfn = 'questions.xml'	# moodle questions xml
    
        qdict = self.load_questions(mdir,qfn)
        self.convert_static_files()
    
        moodx = etree.parse('%s/%s' % (mdir,mfn))
    
        info = moodx.find('.//information')
        name = info.find('.//original_course_fullname').text
        number = info.find('.//original_course_shortname').text
        contents = moodx.find('.//contents')
        number = self.make_url_name(number, extra_ok_chars='.')
    
        # start course.xml
        cxml = etree.Element('course', graceperiod="1 day 5 hours 59 minutes 59 seconds")
        cxml.set('display_name',name)
        cxml.set('number', number)
        cxml.set('org','MITx')
    
        # place each activity as a new sequential inside a chapter
        # the chapter is specified by the section (moodle sectionid)
        # sections is dict with key=sectionid, value=chapter XML
        sections = {}

        self.load_moodle_course_head(cxml)	# load the course/course.xml if it has anything

        seq = None
        for activity in contents.findall('.//activity'):
            seq = self.activity2chapter(activity, sections, cxml, seq, qdict)
            
        chapter = cxml.find('chapter')
        name = name.replace('/',' ')
        chapter.set('name',name)	# use first chapter for course name (FIXME)
    
        cdir = self.edxdir
        semester = self.make_url_name(semester)
        os.popen('xmllint -format -o %s/course/%s.xml -' % (cdir, semester),'w').write(etree.tostring(cxml,pretty_print=True))
            
        # the actual top-level course.xml file is a pointer XML file to the one in course/semester.xml
        open('%s/course.xml' % cdir, 'w').write('<course url_name="%s" org="%s" course="%s"/>\n' % (semester, org, number))


    def convert_static_files(self):
        '''
        Convert moodle static files (from moodle_dir/files/prefix/hash) to edX (edxdir/static/file_name)
        use moodle_dir/files.xml

        Construct dictionary of files by id, with values as static link urls.

        Save that dictionary as self.staticfiles
        '''
        self.staticfiles = {}
        fxml = etree.parse(self.moodle_dir / 'files.xml').getroot()
        if self.verbose:
            print "==== Copying static files"
        for mfile in fxml.findall('file'):
            fhash = mfile.find('contenthash').text
            ftype = mfile.find('mimetype').text
            fname = mfile.find('filename').text	    # instructor supplied filename
            if fname=='.':
                # print "    strange filename '.', skipping..."
                continue
            fname2 = fname.replace(' ', '_')
            fileid = mfile.get('id')
            url = '/static/%s' % fname2
            self.staticfiles[fileid] = (url, fname)
            os.system('cp %s/files/%s/%s "%s/static/%s"' % (self.moodle_dir, fhash[:2], fhash, self.edxdir, fname2))
            if self.verbose:
                print "      %s" % fname
                sys.stdout.flush()
            

    def load_moodle_course_head(self, cxml):
        '''
        load the course/course.xml if it has anything
        '''
        xml = etree.parse('%s/course/course.xml' % (self.moodle_dir)).getroot()
        name = xml.find('shortname').text
        contents = xml.find('summary').text
        if not contents:
            return
        
        chapter = etree.SubElement(cxml,'chapter')
        seq = etree.SubElement(chapter,'sequential')
        seq.set('display_name', name)
        url_name = self.make_url_name('course__' + name, dupok=False)
        self.save_as_html(url_name, name, contents, seq)

    # convert activity to chapter
    
    def activity2chapter(self, activity, sections, cxml, seq, qdict):

        adir = activity.find('directory').text
        title = activity.find('title').text.strip()
        category = activity.find('modulename').text
        sectionid = activity.find('sectionid').text
        
        # new section?
        if not sectionid in sections:
            chapter = etree.SubElement(cxml,'chapter')
            sections[sectionid] = chapter
            seq = self.get_moodle_section(sectionid, chapter, activity_title=title)
        else:
            chapter = sections[sectionid]
    
        if category=='url':
            if seq is None:		# use current sequential if exists, else create new one
                seq = etree.SubElement(chapter,'sequential')
            else:
                print "  ",
            print "  --> URL %s (%s)" % (title,adir)
            self.import_moodle_url(adir, seq)

        elif category=='label':
            if seq is None:		# use current sequential if exists, else create new one
                seq = etree.SubElement(chapter,'sequential')
            else:
                print "  ",
            print "  --> label %s (%s)" % (title,adir)
            self.import_moodle_label(adir, seq)

        elif category=='resource':
            if seq is None:		# use current sequential if exists, else create new one
                seq = etree.SubElement(chapter,'sequential')
            else:
                print "  ",
            print "  --> resource %s (%s)" % (title,adir)
            self.import_moodle_resource(adir, seq)

        elif category=='page':
            print "  --> etext %s (%s)" % (title,adir)
            seq = etree.SubElement(chapter,'sequential')
            self.import_page(adir, seq)
    
        elif category=='quiz':
            if seq is None:		# use current sequential if exists, else create new one
                seq = etree.SubElement(chapter,'sequential')
            else:
                print "  ",
            print "  --> problem %s (%s)" % (title,adir)
            self.import_quiz(adir,seq,qdict)

        else:
            print "  --> unknown activity type %s (adir=%s)" % (category, adir)

        return seq


    def get_moodle_section(self, sectionid, chapter, activity_title=""):
        '''
        sectionid is a number
        '''
        sdir = 'sections/section_%s' % sectionid
        xml = etree.parse('%s/%s/section.xml' % (self.moodle_dir, sdir)).getroot()
        name = xml.find('name').text
        contents = xml.find('summary').text
        contents = contents.replace('<o:p></o:p>','')
        # if moodle author didn't bother to set name, but instead used <h2> then grab name from that
        if not name or name=='$@NULL@$':
            m = re.search('<h2(| align="left")>(.*?)</h2>', contents)
            if m:
                name = html2text.html2text(m.group(2))
                name = name.replace('\n','').replace('\r','')
        if not name or name=='$@NULL@$':
            htext = html2text.html2text(contents)
            # print "Warning: empty name for section %s, contents=%s ..." %  (sectionid, htext.split('\n')[0].strip())
            name = htext[:50].split('\n')[0].strip()
        if not name:
            name = activity_title.strip().split('\n')[0].strip()
        name = name.strip()
        print "--> Section: %s" % name
        chapter.set('display_name', name)
        if contents:
            seq = etree.SubElement(chapter,'sequential')
            seq.set('display_name', name)
            url_name = self.make_url_name('section_%s__%s' % (sectionid, name), dupok=False)
            self.save_as_html(url_name, name, contents, seq)
            return seq
        return None
                
    def get_moodle_page_by_id(self, moduleid):
        '''
        moduleid is a number, eg 110
        '''
        adir = 'activities/page_%s' % moduleid
        return self.get_moodle_page_by_dir(adir)

    def get_moodle_page_by_dir(self, adir, fn='page.xml'):
        pxml = etree.parse('%s/%s/%s' % (self.moodle_dir, adir, fn)).getroot()
        name = pxml.find('.//name').text.strip().split('\n')[0].strip()
        fnpre = os.path.basename(adir) + '__' + name.replace(' ','_').replace('/','_')
        url_name = self.make_url_name(fnpre, dupok=True)
        return pxml, url_name, name

    def import_moodle_url(self, adir, seq):
        pxml, url_name, name = self.get_moodle_page_by_dir(adir, fn='url.xml')
        seq.set('display_name', name)
        url = cgi.escape(pxml.find('.//externalurl').text)
        # html.set('display_name', name)
        htmlstr = pxml.find('.//intro').text or ''
        htmlstr = '<p>%s</p><p><a href="%s">%s</a></p>' % (htmlstr, url, name)
        self.save_as_html(url_name, name, htmlstr, seq)

    def import_moodle_label(self, adir, seq):
        pxml, url_name, name = self.get_moodle_page_by_dir(adir, fn='label.xml')
        seq.set('display_name', name)
        htmlstr = pxml.find('.//intro').text
        self.save_as_html(url_name, name, htmlstr, seq)

    def import_moodle_resource(self, adir, seq):
        pxml, url_name, name = self.get_moodle_page_by_dir(adir, fn='resource.xml')
        seq.set('display_name', name)
        xml = etree.parse('%s/%s/%s' % (self.moodle_dir, adir, 'inforef.xml')).getroot()
        htmlstr = '<h2>%s</h2>' % cgi.escape(name)
        for fileid in xml.findall('.//id'):
            fidnum = fileid.text
            (url, filename) = self.staticfiles.get(fidnum, ('',''))
            # print "fileid: %s -> %s" % (fidnum, self.staticfiles.get(fidnum))
            htmlstr += '<p><a href="%s">%s</a></p>' % (url, filename)
        self.save_as_html(url_name, name, htmlstr, seq)

    def import_page(self, adir, seq):
        pxml, url_name, name = self.get_moodle_page_by_dir(adir)
        seq.set('display_name', name)
        # html.set('display_name', name)
        htmlstr = pxml.find('.//content').text
        self.save_as_html(url_name, name, htmlstr, seq)

    def save_as_html(self, url_name, name, htmlstr, seq):
        '''
        Add a "html" element to the sequential seq, with url_name
        Save the htmlstr contents to a new HTML file, with url_name

        Used for both moodle pages and moodle sections (which contain intro material)
        '''
        vert = etree.SubElement(seq,'vertical')
        html = etree.SubElement(vert,'html')
        htmlstr = htmlstr.replace('<o:p></o:p>','')
        # htmlstr = saxutils.unescape(htmlstr)
        
        # links to static files
        # htmlstr = htmlstr.replace('@@PLUGINFILE@@','/static')
        def fix_static_src(m):
            return ' src="/static/%s"' % (m.group(1).replace('%20','_'))
        htmlstr = re.sub(' src="@@PLUGINFILE@@/([^"]+)"', fix_static_src, htmlstr)

        # relative links to pages
        # href="$@PAGEVIEWBYID*117@$"
        def fix_relative_link(m):
            moodle_id = m.group(1)
            rel_pxml, rel_url_name, rel_name = self.get_moodle_page_by_id(moodle_id)
            return ' href="/jump_to_id/%s"' % (rel_url_name)
        htmlstr = re.sub(' href="\$@PAGEVIEWBYID\*([^"]+)@\$"', fix_relative_link, htmlstr)

        htmlstr = (u'<html display_name="%s">\n' % cgi.escape(name)) + htmlstr + u'\n</html>'
        codecs.open('%s/html/%s.xml' % (self.edxdir, url_name),'w',encoding='utf8').write(htmlstr)
        html.set('url_name','%s' % url_name)
        vert.set('url_name', 'vert_%s' % url_name)
    
    def import_quiz(self, adir,seq,qdict):
        qxml = etree.parse('%s/%s/quiz.xml' % (self.moodle_dir, adir)).getroot()
        name = qxml.find('.//name').text
        seq.set('name',name)
        # TODO: import intro, do points
        for qinst in qxml.findall('.//question_instance'):
            qnum = qinst.find('question').text
            question = qdict[qnum]
            vert = etree.SubElement(seq,'vertical')	# one problem in each vertical
            problem = etree.SubElement(vert,'problem')
            problem.set('rerandomize',"never")
            problem.set('showanswer','attempted')
            qname = question.find('name').text
            # problem.set('name',qname)
            qfn = question.get('filename')
            url_name = self.make_url_name(qfn.replace('.xml',''))
            problem.set('url_name', url_name)
            print "    --> question: %s (%s)" % (qname, url_name)
            self.export_question(question, qname, url_name)
    
    #-----------------------------------------------------------------------------
    # write out question as an xml file
    
    @staticmethod
    def fix_math(s):
        '''
        attempt to turn $$xxx$$ into [mathjax]xxx[/mathjax]
        '''
        s = re.sub('\$\$([^\$]*?)\$\$','[mathjax]\\1[/mathjax]',s)
        return s
    
    def export_question(self, question, name="", url_name=""):
        problem = etree.Element('problem')
        problem.set('display_name', name)
        text = etree.SubElement(problem,'text')
        qtext = question.find('questiontext').text
        qtext = self.fix_math(qtext)
        qtext = '<html>%s</html>' % qtext
        # qtext = saxutils.unescape(qtext)
        text.append(etree.XML(qtext))
        qtype = question.find('.//qtype').text
    
        if qtype=='truefalse':
            options = []
            expect = ""
            for answer in question.findall('.//answer'):
                op = answer.find('answertext').text
                options.append(op)
                if float(answer.find('fraction').text)==1.0:
                    expect = str(op)
            optionstr = ','.join(['"%s"' % x.replace('"',"'") for x in options])
            abox = AnswerBox("type='option' expect='%s' options=%s" % (expect,optionstr))
            problem.append(abox.xml)
    
        elif qtype=='multichoice':
            options = []
            expect = ""
            for answer in question.findall('.//answer'):
                op = answer.find('answertext').text
                op = op.replace(u'\xa0',' ')
                options.append(op)
                if float(answer.find('fraction').text)==1.0:
                    expect = str(op)
            optionstr = ','.join(['"%s"' % x.replace('"',"'") for x in options])
            abox = AnswerBox("type='multichoice' expect='%s' options=%s" % (expect,optionstr))
            problem.append(abox.xml)
    
        pfn = url_name
        os.popen('xmllint -format -o %s/problem/%s.xml -' % (self.edxdir, pfn),'w').write(etree.tostring(problem,pretty_print=True))
        print "        wrote %s" % pfn
            
    
    #-----------------------------------------------------------------------------
    # load all questions
    
    def load_questions(self, dir,qfn):
        qdict = {}
        moodq = etree.parse('%s/%s' % (dir,qfn))
        for question in moodq.findall('.//question'):
            id = question.get('id')
            if id is None: continue
            qdict[id] = question
            try:
                name = question.find('.//name').text
                question.set('filename',name.replace(' ','_').replace('.','_') + '.xml')
            except Exception as err:
                print "** Error: can't get name for question id=%s" % question.get('id')
        return qdict
    
    #----------------------------------------
    
    def make_url_name(self, s, tag='', dupok=False, extra_ok_chars=""):
        '''
        Turn string s into a valid url_name.
        Use tag if provided.
        '''
        map = {'"\':<>': '',
               ',/().;=+ ': '_',
               '/': '__',
               '*': '',
               '?': '',
               '&': 'and',
               '#': '_num_',
               '[': 'LB_',
               ']': '_RB',
               }
        if not s:
            s = tag
        for m,v in map.items():
            for ch in m:
                s = s.replace(ch,v)

        if s=='':
            s = 'x'

        snew = ''
        for ch in s:
            if not ch in string.lowercase + string.uppercase + string.digits + '-_ ' + extra_ok_chars:
                ch = ''
            snew += ch
        s = snew

        if (not dupok) and s in self.URLNAMES and not s.endswith(tag):
            s = '%s_%s' % (tag, s)
        while (not dupok) and (s in self.URLNAMES):
            s += 'x'
        if not s in self.URLNAMES:
            self.URLNAMES.append(s)
        return s


#--------------------------------------------------------------------------
# main

if __name__=='__main__':
    if len(sys.argv)<2:
        print "Usage: python moodle2edx.py [ backup-file.mbz | backup-directory ]"
        sys.exit(0)
    infn = sys.argv[1]
    sys.argv.pop(1)
    edxdir = '.'
    if len(sys.argv)>1:
        edxdir = sys.argv[1]

    print "importing %s to %s" % (infn, edxdir)

    m2e = Moodle2Edx(infn, edxdir)
    
