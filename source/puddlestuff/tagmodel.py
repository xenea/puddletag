# -*- coding: utf-8 -*-
#tagmodel.py

#Copyright (C) 2008-2009 concentricpuddle

#This file is part of puddletag, a semi-good music tag editor.

#This program is free software; you can redistribute it and/or modify
#it under the terms of the GNU General Public License as published by
#the Free Software Foundation; either version 2 of the License, or
#(at your option) any later version.

#This program is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNE SS FOR A PARTICULAR PURPOSE.  See the
#GNU General Public License for more details.

#You should have received a copy of the GNU General Public License
#along with this program; if not, write to the Free Software
#Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

from PyQt4.QtGui import *
from PyQt4.QtCore import *
import sys,os, audioinfo, resource, pdb
from operator import itemgetter
from copy import copy, deepcopy
from subprocess import Popen
from os import path
from audioinfo import (PATH, FILENAME, DIRPATH, EXTENSION,
                        usertags, setmodtime, FILETAGS, READONLY)
from puddleobjects import (unique, safe_name, partial, natcasecmp, gettag,
                           HeaderSetting, getfiles, ProgressWin, PuddleStatus,
                           PuddleThread, progress, PuddleConfig)
from musiclib import MusicLibError
import time
from errno import EEXIST
import traceback

status = {}

SETDATAERROR = SIGNAL("setDataError")
LIBRARY = '__library'
ENABLEUNDO = SIGNAL('enableUndo')

def loadsettings(filepath=None):
    settings = PuddleConfig()
    if filepath:
        setting.filename = filepath
    titles = settings.get('tableheader', 'titles',
                            ['Path', 'Artist', 'Title', 'Album', 'Track',
                                'Length', 'Year', 'Bitrate', 'Genre',
                                'Comment', 'Filename'])
    tags = settings.get('tableheader', 'tags',
                        ['__path', 'artist', 'title',
                            'album', 'track', '__length', 'year', '__bitrate',
                            'genre', 'comment', '__filename'])
    checked = settings.get('tableheader', 'enabled', range(len(tags)), True)
    return (zip(titles, tags), checked)

class Properties(QDialog):
    def __init__(self, info, parent=None):
        """Shows a window with the properties in info.

        info should be a list of 2-entry tuples. These tuples should consists
        of a string in the 0-th index to be used as the title of a group.
        The first index (the properties) should also be a list length 2 tuples.
        Both indexes containing strings. Where the 0-th is used as the description
        and the other as the value.

        .e.g
        [('File', [('Filename', u'/Hip Hop Songs/Nas-These Are Our Heroes .mp3'),
                  ('Size', u'6151 kB'),
                  ('Path', u'Nas - These Are Our Heroes .mp3'),
                  ('Modified', '2009-07-28 14:04:05'),
                  ('ID3 Version', u'ID3v2.4')]),
        ('Version', [('Version', u'MPEG 1 Layer 3'),
                    ('Bitrate', u'192 kb/s'),
                    ('Frequency', u'44.1 kHz'),
                    ('Mode', 'Stereo'),
                    ('Length', u'4:22')])]
        """
        QDialog.__init__(self,parent)
        self._load(info)

    def _load(self, info):
        vbox = QVBoxLayout()
        interaction = Qt.TextSelectableByMouse or Qt.TextSelectableByKeyboard
        for title, items in info:
            frame = QGroupBox(title)
            framegrid = QGridLayout()
            framegrid.setColumnStretch(1,1)
            for row, value in enumerate(items):
                property = QLabel(value[0] + u':')
                property.setTextInteractionFlags(interaction)
                framegrid.addWidget(property, row, 0)
                propvalue = QLabel(u'<b>%s</b>' % value[1])
                propvalue.setTextInteractionFlags(interaction)
                framegrid.addWidget(propvalue, row, 1)
            frame.setLayout(framegrid)
            vbox.addWidget(frame)
        close = QPushButton('Close')
        self.connect(close, SIGNAL('clicked()'), self.close)
        hbox = QHBoxLayout()
        hbox.addStretch()
        hbox.addWidget(close)
        vbox.addLayout(hbox)
        self.setLayout(vbox)


class ColumnSettings(HeaderSetting):
    """A dialog that allows you to edit the header of a TagTable widget."""
    title = 'Columns'
    def __init__(self, parent = None, showok = False, status=None):
        self.tags, checked = loadsettings()
        HeaderSetting.__init__(self, self.tags, parent, showok, True)
        if parent:
            self.setWindowFlags(Qt.Widget)
        label = QLabel('Adjust visibility of columns.')
        self.grid.addWidget(label, 0, 0)
        items = [self.listbox.item(z) for z in range(self.listbox.count())]
        if not checked:
            checked = []
        [z.setCheckState(Qt.Checked) if i in checked else z.setCheckState(Qt.Unchecked)
                                for i,z in enumerate(items)]

    def applySettings(self, control):
        row = self.listbox.currentRow()
        if row > -1:
            self.tags[row][0] = unicode(self.textname.text())
            self.tags[row][1] = unicode(self.tag.text())
        checked = [z for z in range(self.listbox.count()) if self.listbox.item(z).checkState()]
        titles = [z[0] for z in self.tags]
        tags = [z[1] for z in self.tags]
        cparser = PuddleConfig()
        cparser.set('tableheader', 'titles', titles)
        cparser.set('tableheader', 'tags', tags)
        cparser.set('tableheader', 'enabled', checked)

        if control:
            control.setHeaderTags([z for i, z in enumerate(self.tags) if i in checked])
        else:
            self.emit(SIGNAL("headerChanged"), headerdata)

    def add(self):
        row = self.listbox.count()
        self.tags.append(["",""])
        item = QListWidgetItem('')
        item.setCheckState(True)
        self.listbox.addItem(item)
        self.listbox.clearSelection()
        self.listbox.setCurrentRow(row)
        self.textname.setFocus()

    def okClicked(self):
        self.applySettings()
        self.close()

class TagModel(QAbstractTableModel):
    """The model used in TableShit
    Methods you shoud take not of are(read docstrings for more):

    setData -> As per the usual model, can only write one tag at a time.
    setRowData -> Writes a row's tags at once.
    undo -> undo's changes
    setTestData and unSetTestData -> Used to display temporary values in the table.
    """
    def __init__(self, headerdata, taginfo = None):
        """Load tags.

        headerdata must be a list of tuples
        where the first item is the displayrole and the second
        the tag to be used as in [("Artist", "artist"), ("Title", title")].

        taginfo is a list of audioinfo.Tag objects."""

        QAbstractTableModel.__init__(self)
        self.headerdata = headerdata
        self.colorRows = []
        self.sortOrder = (0, Qt.AscendingOrder)
        self.saveModification = True
        if taginfo is not None:
            self.taginfo = unique(taginfo)
            self.sort(*self.sortOrder)
        else:
            self.taginfo = []
            self.reset()
        for z in self.taginfo:
            z.testData = {}
        self.undolevel = 0
        self.testData = {}
        self._fontSize = QFont().pointSize()

    def _setFontSize(self, size):
        self._fontSize = size
        top = self.index(self.rowCount(), 0)
        bottom = self.index(self.rowCount() -1, self.columnCount() -1)
        self.emit(SIGNAL("dataChanged(QModelIndex,QModelIndex)"),
        top, bottom)

    def _getFontSize(self):
        return self._fontSize

    fontSize = property(_getFontSize, _setFontSize)

    def _getUndoLevel(self):
        return self._undolevel

    def _setUndoLevel(self, value):
        #print value
        if value == 0:
            self.emit(ENABLEUNDO, False)
        else:
            self.emit(ENABLEUNDO, True)
        self._undolevel = value

    undolevel = property(_getUndoLevel, _setUndoLevel)

    def changeFolder(self, olddir, newdir):
        """Used for changing the directory of all the files in olddir to newdir.
        i.e. All children of olddir will now become children of newdir

        No actual moving is done though."""

        folder = itemgetter(DIRPATH)
        tags = [z for z in self.taginfo if folder(z).startswith(olddir)]
        libtags = []
        for audio in tags:
            if folder(audio) == olddir:
                audio[FILENAME] = path.join(newdir, audio[PATH])
                audio[DIRPATH] = newdir
            else: #Newdir is a parent
                audio[DIRPATH] = newdir + folder(audio)[len(olddir):]
                audio[FILENAME] = path.join(folder(audio), audio[PATH])
            if '__library' in audio:
                audio.save(True)
        self.reset()

    def columnCount(self, index=QModelIndex()):
        return len(self.headerdata)

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if not index.isValid() or not (0 <= row < len(self.taginfo)):
            return QVariant()
        if (role == Qt.DisplayRole) or (role == Qt.ToolTipRole) or (role == Qt.EditRole):
            try:
                audio = self.taginfo[row]
                tag = self.headerdata[index.column()][1]
                if tag in audio.testData:
                    val = audio.testData[tag]
                else:
                    val = audio[tag]

                if isinstance(val, basestring):
                    return QVariant(val)
                else:
                    return QVariant(val[0])
            except (KeyError, IndexError):
                return QVariant()
        elif role == Qt.BackgroundColorRole:
            try:
                return QVariant(QColor(self.taginfo[row].color))
            except AttributeError:
                return QVariant()
        elif role == Qt.FontRole:
            tag = self.headerdata[index.column()][1]
            f = QFont()
            if f.pointSize() != self.fontSize:
                f.setPointSize(self.fontSize)
            if tag in self.taginfo[row].testData:
                f.setBold(True)
            return QVariant(f)
        return QVariant()

    def deleteTag(self, row):
        audio = self.taginfo[row]
        uns = dict([(key, val) for key,val in audio.items()
                            if isinstance(key, (int, long))])
        tags = audio.usertags
        tags['__image'] = audio['__image']
        audio.delete()
        audio[self.undolevel] = tags
        audio.update(uns)

    def deleteTags(self, rows):
        [self.deleteTag(row) for row in rows]
        self.undolevel += 1

    def dropMimeData(self, data, action, row, column, parent = QModelIndex()):
        return True

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsEnabled
        return Qt.ItemFlags(QAbstractTableModel.flags(self, index)|
                            Qt.ItemIsEditable| Qt.ItemIsDropEnabled)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.TextAlignmentRole:
            if orientation == Qt.Horizontal:
                return QVariant(int(Qt.AlignLeft|Qt.AlignVCenter))
            return QVariant(int(Qt.AlignRight|Qt.AlignVCenter))
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            try:
                return QVariant(self.headerdata[section][0])
            except IndexError:
                return QVariant()
        return QVariant(long(section + 1))

    def insertColumns(self, column, count, parent = QModelIndex(), data=None):
        self.beginInsertColumns (parent, column, column + count)
        if data:
            self.headerdata.extend(data)
        else:
            self.headerdata += [("","") for z in range(count - column+1)]
        self.endInsertColumns()
        #self.emit(SIGNAL('modelReset')) #Because of the strange behaviour mentioned in reset.
        return True

    def load(self,taginfo,headerdata=None, append = False):
        """Loads tags as in __init__.
        If append is true, then the tags are just appended."""
        if headerdata is not None:
            self.headerdata = headerdata

        for z in taginfo:
            z.testData = {}

        if append:
            top = self.index(self.rowCount(), 0)
            column = self.sortOrder[0]
            tag = self.headerdata[column][1]
            if self.sortOrder[1] == Qt.AscendingOrder:
                taginfo = sorted(taginfo, natcasecmp, itemgetter(tag))
            else:
                taginfo = sorted(self.taginfo, natcasecmp, itemgetter(tag), True)
            filenames = [z[FILENAME] for z in self.taginfo]
            self.taginfo.extend([z for z in taginfo if z[FILENAME] not in filenames])
            rowcount = self.rowCount()
            self.beginInsertRows(QModelIndex(), rowcount, rowcount + len(taginfo) - 1)
            self.endInsertRows()
            bottom = self.index(self.rowCount() -1, self.columnCount() -1)
            self.emit(SIGNAL("dataChanged(QModelIndex,QModelIndex)"),
                                            top, bottom)
        else:
            top = self.index(0, 0)
            self.taginfo = taginfo
            self.reset()
            self.sort(*self.sortOrder)
            self.undolevel = 0

    def removeColumns(self, column, count, parent = QModelIndex()):
        """This function only allows removal of one column at a time.
        For some reason, it just clears the columns otherwise.
        So for now, this seems to work."""
        self.beginRemoveColumns(QModelIndex(), column, column + count - 1)
        del(self.headerdata[column: column+count])
        self.endRemoveColumns()
        return True

    def removeFolders(self, folders, v = True):
        if v:
            f = [i for i, tag in enumerate(self.taginfo) if tag[DIRPATH]
                                    not in folders and '__library' not in tag]
        else:
            f = [i for i, tag in enumerate(self.taginfo) if tag[DIRPATH]
                                        in folders and '__library' not in tag]
        while f:
            try:
                self.removeRows(f[0], delfiles = False)
                del(f[0])
                f = [z - 1 for z in f]
            except IndexError:
                break

    def removeRows(self, position, rows=1, index=QModelIndex(), delfiles = True, msgparent = None):
        """Please, only use this function to remove one row at a time. For some reason, it doesn't work
        too well on debian if more than one row is removed at a time."""
        self.beginRemoveRows(QModelIndex(), position,
                         position + rows -1)
        if delfiles:
            audio = self.taginfo[position]
            os.remove(audio[FILENAME])
            if LIBRARY in audio:
                self.emit(SIGNAL('delLibFile'), [audio])
        del(self.taginfo[position])
        self.endRemoveRows()
        return True

    def renameFile(self, row, tags):
        """If tags(a dictionary) contains a PATH key, then the file
        in self.taginfo[row] is renamed based on that.

        If successful, tags is returned(with the new filename as a key)
        otherwise {} is returned."""
        currentfile = self.taginfo[row]
        oldfilename = currentfile.filepath

        if PATH in tags:
            currentfile.filename = tags[PATH]
        elif FILENAME in tags:
            currentfile.filepath = tags[PATH]
        elif EXTENSION in tags:
            currentfile.ext = tags[EXTENSION]
        else:
            return

        newfilename = currentfile.filepath
        if newfilename != oldfilename:
            try:
                if os.path.exists(newfilename) and newfilename != oldfilename:
                    raise IOError(EEXIST, os.strerror(EEXIST), oldfilename)
                os.rename(oldfilename, newfilename)
            #I don't want to handle the error, but at the same time I want to know
            #which file the error occured at.
            except (IOError, OSError), detail:
                currentfile.filename = oldfilename
                self.emit(SIGNAL('fileError'), currentfile)
                raise detail

    def reset(self):
        #Sometimes, (actually all the time on my box, but it may be different on yours)
        #if a number files loaded into the model is equal to number
        #of files currently in the model then the TableView isn't updated.
        #Why the fuck I don't know, but this signal, intercepted by the table,
        #updates the view and makes everything work okay.
        self.emit(SIGNAL('modelReset'))
        QAbstractTableModel.reset(self)

    def rowColors(self, rows = None, clear=False):
        """Changes the background of rows to green.

        If rows is None, then the background of all the rows in the table
        are returned to normal."""
        taginfo = self.taginfo
        if rows:
            if clear:
                for row in rows:
                    if hasattr(taginfo[row], "color"):
                        del(taginfo[row].color)
            else:
                for row in rows:
                    self.taginfo[row].color = Qt.green
                for i, z in enumerate(taginfo):
                    if i not in rows and hasattr(taginfo[row], "color"):
                        del(taginfo[row].color)
        else:
            rows = []
            for i, tag in enumerate(self.taginfo):
                if hasattr(tag, 'color'):
                    del(tag.color)
                    rows.append(i)
        if rows:
            firstindex = self.index(min(rows), 0)
            lastindex = self.index(max(rows), self.columnCount() - 1)
            self.emit(SIGNAL("dataChanged(QModelIndex,QModelIndex)"),
                                            firstindex, lastindex)

    def rowCount(self, index = QModelIndex()):
        return len(self.taginfo)

    def setData(self, index, value, role = Qt.EditRole):
        """Sets the data of the currently edited cell as expected.
        Also writes tags and increases the undolevel."""
        if index.isValid() and 0 <= index.row() < len(self.taginfo):
            column = index.column()
            tag = self.headerdata[column][1]
            currentfile = self.taginfo[index.row()]
            if tag in currentfile.revmapping:
                oldtag = currentfile.real(tag)
                if oldtag in READONLY:
                    return False
            else:
                if tag in READONLY:
                    return False
                oldtag = ''

            filename = currentfile.filepath
            newvalue = unicode(value.toString())
            #Tags that startwith "__" are usually read only except for __path
            #in which case we rename the files.
            try:
                oldvalue = deepcopy(currentfile[tag])
            except KeyError:
                oldvalue = [""]

            if oldtag in FILETAGS:
                try:
                    self.setRowData(index.row(), {tag: newvalue}, True, True)
                    self.undolevel += 1
                    return True
                except (IOError, OSError), detail:
                    self.emit(SETDATAERROR, index.row(), column, "Couldn't rename " + filename + ": " + detail.strerror)
                    return False
            try:
                currentfile[tag] = newvalue
                currentfile.save()
            except (IOError, OSError), detail:
                currentfile[tag] = oldvalue
                self.emit(SETDATAERROR, index.row(), column, "Couldn't write to " + filename + ": " + detail.strerror)
                return False

            currentfile[self.undolevel] = {tag: oldvalue}
            self.emit(SIGNAL('fileChanged()'))
            if LIBRARY in currentfile:
                oldfile = currentfile.tags.copy()
                oldfile.update(currentfile[self.undolevel])
                self.emit(SIGNAL('libFileChanged'), [oldfile], [currentfile])
            self.undolevel += 1
            if self.saveModification:
                setmodtime(currentfile.filepath, currentfile['__accessed'], currentfile['__modified'])
            self.emit(SIGNAL("dataChanged(QModelIndex,QModelIndex)"),
                                        index, index)
            return True
        return False

    def setHeaderData(self, section, orientation, value, role = Qt.EditRole):
        if (orientation == Qt.Horizontal) and (role == Qt.DisplayRole):
            self.headerdata[section] = value
        self.emit(SIGNAL("headerDataChanged (Qt::Orientation,int,int)"), orientation, section, section)

    def setHeader(self, tags):
        self.headerdata = tags
        self.reset()

    def setRowData(self,row, tags, undo = False, justrename = False):
        """A function to update one row.
        row is the row, tags is a dictionary of tags.

        If undo`is True, then an undo level is created for this file.
        If justrename is True, then (if tags contain a PATH or EXTENSION key)
        the file is just renamed i.e not tags are written.
        """
        currentfile = self.taginfo[row]
        filetags = {}
        if currentfile.mapping:
            revmapping, real = currentfile.revmapping, currentfile.real
            filetags = dict([(revmapping[key], tags[key]) for key in FILETAGS
                                if key in tags and key in revmapping])
        audio = tags.copy()

        if DIRPATH in audio:
            del(audio[DIRPATH])
        if undo:
            oldtag = currentfile
            oldtag = dict([(tag, copy(currentfile[tag])) if tag in currentfile
                            else (tag, []) for tag in tags])
            if self.undolevel in oldtag:
                currentfile[self.undolevel].update(oldtag)
            else:
                currentfile[self.undolevel] = oldtag
        oldimages = None
        if '__image' in audio:
            oldimages = audio['__image']
            if not hasattr(currentfile, 'image'):
                del(audio['__image'])
            else:
                images = []
                for z in audio['__image']:
                    images.append(dict([(key,val) for key,val in z.items()
                                            if key in currentfile.IMAGETAGS]))
                audio['__image'] = [currentfile.image(**z) for z in images]
        if filetags:
            self.renameFile(row, filetags)
        if justrename and LIBRARY in currentfile:
            currentfile.save(True)
        if not justrename:
            try:
                currentfile.update(audio)
                currentfile.save()
                if self.saveModification:
                    setmodtime(currentfile.filepath, currentfile['__accessed'],
                                currentfile['__modified'])
            except (OSError, IOError), detail:
                currentfile.update(currentfile[self.undolevel])
                del(currentfile[self.undolevel])
                raise detail
        if oldimages is not None:
            audio['__image'] = oldimages

    def setTestData(self, rows, tags):
        """A method that allows you to change the visible data of
        the model without writing tags.

        rows is the rows that you want to change
        tags -> is the tags that are to be shown.

        If you want want to write the values that you showed
        call unsetData with write = True.

        However, if you just want to return to the previous
        view, call unsetData with write = False, and if you want,
        the rows you want to return to normal.

        Note, that if the user changed anything during this
        process, then those changes are left alone."""

        unsetrows = rows[len(tags):]
        taginfo = self.taginfo
        if unsetrows:
            self.unSetTestData(rows = unsetrows)
        for row, tag in zip(rows, tags):
            taginfo[row].testData = tag
        firstindex = self.index(min(rows), 0)
        lastindex = self.index(max(rows),self.columnCount() - 1)
        self.emit(SIGNAL("dataChanged(QModelIndex,QModelIndex)"),
                                        firstindex, lastindex)

    def sibling(self, row, column, index = QModelIndex()):
        if row < (self.rowCount() - 1) and row >= 0:
           return self.index(row + 1, column)

    def sort(self,column, order = Qt.DescendingOrder):
        self.sortOrder = (column, order)
        try:
            tag = self.headerdata[column][1]
        except IndexError:
            if len(self.headerdata) >= 1:
                tag = self.headerdata[0][1]
            else:
                return
        if order == Qt.AscendingOrder:
            self.taginfo = sorted(self.taginfo, natcasecmp, itemgetter(tag))
        else:
            self.taginfo = sorted(self.taginfo, natcasecmp, itemgetter(tag), True)
        self.reset()

    def supportedDropActions(self):
        return Qt.CopyAction

    def undo(self):
        """Undos the last action.

        Basically, if a tag has a key which is = self.undolevel - 1,
        then the tag is updated with the dictionary in that key.

        setRowData does not modify the undoleve unless you explicitely tell
        it, but setData does modify the undolevel.

        It is recommended that you use consecutive indexes for self.undolevel."""
        if self.undolevel <= 0:
            self.undolevel = 0
            return
        level = self.undolevel - 1
        oldfiles =  []
        newfiles = []
        rows = []
        for row, audio in enumerate(self.taginfo):
            if level in audio:
                if LIBRARY in audio:
                    oldfiles.append(audio.tags.copy())
                self.setRowData(row, audio[level])
                rows.append(row)
                del(audio[level])
                if LIBRARY in audio:
                    newfiles.append(audio.tags.copy())
        if rows:
            self.updateTable(rows)
            if oldfiles:
                self.emit(SIGNAL('libFileChanged'), oldfiles, newfiles)
        if self.undolevel > 0:
            self.undolevel -= 1

    def unSetTestData(self, write = False, rows = None):
        """See testData for info on how to use this function.

        Note that if write is True then a function is returned.
        It accepts an argument to be used as parent for a progress dialog
        to be shown."""
        taginfo = self.taginfo
        if write:
            if not rows:
                rows = [i for i,z in enumerate(taginfo) if z.testData]
            def what():
                for row in rows:
                    try:
                        self.setRowData(row, taginfo[row].testData, True)
                        taginfo[row].testData = {}
                        yield None
                    except (OSError, IOError), e:
                        errmsg = u"I couldn't write to <b>%s</b>. (%s)" % (
                                            taginfo[row][FILENAME], e.strerror)
                        yield (errmsg, len(rows))
                if rows:
                    self.undolevel += 1
            return progress(what, 'Writing ', len(rows), lambda: self.updateTable(rows))
        else:
            if not rows:
                rows = [i for i,z in enumerate(taginfo) if z.testData]
            for row in rows:
                taginfo[row].testData = {}

        if rows:
            firstindex = self.index(min(rows), 0)
            lastindex = self.index(max(rows), self.columnCount() - 1)
            self.emit(SIGNAL("dataChanged(QModelIndex,QModelIndex)"),
                                            firstindex, lastindex)

    def updateTable(self, rows):
        firstindex = self.index(min(rows), 0)
        lastindex = self.index(max(rows),self.columnCount() - 1)
        self.emit(SIGNAL("dataChanged(QModelIndex,QModelIndex)"),
                                        firstindex, lastindex)

class TagDelegate(QItemDelegate):
    def __init__(self,parent=None):
        QItemDelegate.__init__(self,parent)

    def createEditor(self,parent,option,index):
        editor = QLineEdit(parent)
        editor.setFrame(False)
        editor.installEventFilter(self)
        return editor

    def keyPressEvent(self, event):
        QItemDelegate.keyPressEvent(self, event)

    def commitAndCloseEditor(self):
        editor = self.sender()
        self.emit(SIGNAL("closeEditor(QWidget*, QAbstractItemDelegate::EndEditHint)"), editor, QItemDelegate.EditNextItem)

    def setEditorData(self, editor, index):
        text = index.model().data(index, Qt.EditRole).toString()
        editor.setText(text)

    def setModelData(self, editor, model, index):
        model.setData(index, QVariant(editor.text()))


class TableHeader(QHeaderView):
    """A headerview put here simply to enable the contextMenuEvent
    so that I can show the edit columns menu.

    Call it with tags in the usual form, to set the top header."""
    def __init__(self, orientation, tags = None, parent = None):
        QHeaderView.__init__(self, orientation, parent)
        if tags is not None: self.tags = tags
        self.setClickable(True)
        self.setHighlightSections(True)
        self.setMovable(True)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        settings = menu.addAction("&Select Columns")
        self.connect(settings, SIGNAL('triggered()'), self.setTitles)
        menu.exec_(event.globalPos())

    def mousePressEvent(self,event):
        if event.button == Qt.RightButton:
            self.contextMenuEvent(event)
            return
        self.emit(SIGNAL('saveSelection'))
        QHeaderView.mousePressEvent(self, event)

    def setTitles(self):
        self.win = ColumnSettings(showok = True)
        self.win.setModal(True)
        self.win.show()
        self.connect(self.win, SIGNAL("headerChanged"), self.headerChanged)

    def headerChanged(self, val):
        self.emit(SIGNAL("headerChanged"), val)

class TagTable(QTableView):
    """I need a more descriptive name for this.

    This table is the table that handles all my tags for me.
    The main functions and properties are:

    rowTags(row) -> Returns the tags from a row.
    updateRow(row, tags) - > Updates a row with the tags specified
    selectedRows -> A list of currently selected rows
    remRows() -> Removes the selected rows.
    playcommand -> Command to run to play files.
    """

    def __init__(self, headerdata = None, parent = None):
        QTableView.__init__(self,parent)
        self.settingsdialog = ColumnSettings
        self.saveModification = True

        self.emits = ['dirschanged', 'tagselectionchanged', 'filesloaded',
                      'viewfilled', 'filesselected', 'enableUndo', 'dirsmoved']
        self.receives = [('loadFiles', self.loadFiles),
                         ('removeFolders', self.removeFolders)]
        self.gensettings = [('Subfolders', True)]
        if not headerdata:
            headerdata = []
        header = TableHeader(Qt.Horizontal, headerdata, self)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(True)
        self.connect(header, SIGNAL("sectionClicked(int)"), self.sortByColumn)
        #header.setSortIndicator(0, Qt.AscendingOrder)
        self.setHorizontalHeader(header)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setAlternatingRowColors(True)
        self.showmsg = True
        self._currentcol = {}
        self._currentrow = {}
        self._currenttags = []
        self.dirs = []
        self._resize = False
        self._restore = False
        self._selectedRows = []
        self._selectedColumns = []
        self.setHorizontalScrollMode(self.ScrollPerPixel)

        model = TagModel(headerdata)
        self.connect(header, SIGNAL("headerChanged"), self.setHeaderTags)
        self.setModel(model)

        def emitundo(val):
            self.emit(ENABLEUNDO, val)
            self.selectionChanged()
        self.connect(model, ENABLEUNDO, emitundo)
        self.undo = model.undo

        delegate = TagDelegate(self)
        self.setItemDelegate(delegate)
        self.subFolders = False

        self.play = QAction("&Play", self)
        self.play.setShortcut('Ctrl+p')
        self.exttags = QAction("E&xtended Tags", self)
        self.delete = QAction(QIcon(':/remove.png'), '&Delete', self)
        self.delete.setShortcut('Delete')
        self.cleartag = QAction('Delete Tag', self)
        self.cleartag.setShortcut('Ctrl+R')
        self.properties = QAction('Properties', self)

        connect = lambda a,f: self.connect(a, SIGNAL('triggered()'), f)

        connect(self.play, self.playFiles)
        connect(self.delete, self.deleteSelected)
        #connect(self.cleartag, self.clearTags)
        connect(self.properties, self.showProperties)

        def sep():
            separator = QAction(self)
            separator.setSeparator(True)
            return separator

        self.actions = [self.play, self.exttags, self.cleartag,
                        sep(), self.delete, sep(), self.properties]
        status['selectedrows'] = self._getSelectedRows
        status['selectedfiles'] = self._selectedTags
        status['selectedcolumns'] = self._getSelectedColumns
        status['selectedtags'] = self._getSelectedTags
        status['alltags'] = lambda: self.model().taginfo

    def _setFontSize(self, size):
        self.model().fontSize = size

    def _getFontSize(self):
        return self.model().fontSize

    fontSize = property(_getFontSize, _setFontSize)

    def increaseFont(self):
        self.fontSize += 1

    def decreaseFont(self):
        self.fontSize -= 1

    def _getResize(self):
        return self._resize

    def _setResize(self, value):
        self._resize = value
        if value:
            self.resizeColumnsToContents()

    def _getSelectedTags(self):
        htags = [z[1] for z in self.model().headerdata]
        rows = self.currentRowSelection()
        audios = self.selectedTags

        ret = []
        for f, row in zip(audios, sorted(rows)):
            tags = (htags[column] for column in rows[row])
            ret.append(dict([(tag, f[tag]) for tag  in tags]))
        return ret

    def applyGenSettings(self, settings):
        self.subFolders = settings['Subfolders']


    autoresize = property(_getResize, _setResize)

    def changeFolder(self, olddir, newdir):
      try:
        for i, d in enumerate(self.dirs[::]):
            if d == olddir:
                self.dirs[i] = newdir
            elif d.startswith(olddir):
                self.dirs[i] = newdir + d[len(olddir):]
      except IndexError:
        pass
      self.model().changeFolder(olddir, newdir)
      self.emit(SIGNAL('dirsmoved'), olddir, newdir)

    def clearAll(self):
        self.model().taginfo = []
        self.model().reset()
        self.emit(SIGNAL('dirschanged'), [])

    def removeTags(self):
        deltag = self.model().deleteTag
        def func():
            for row in self.selectedRows:
                try:
                    deltag(row)
                    yield None
                except (OSError, IOError), e:
                    yield "There was an error deleting the tag of %s: <b>%s</b>" % (
                                e.filename, e.strerror), len(self.selectedRows)
            self.model().undolevel += 1
            self.selectionChanged()

        f = progress(func, 'Deleting tag... ', len(self.selectedRows))
        f(self)

    def columnCount(self):
        return self.model().columnCount()

    def rowCount(self):
        return self.model().rowCount()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        [menu.addAction(z) for z in self.actions]
        menu.exec_(event.globalPos())

    def _isEmpty(self):
        if self.model().rowCount() <= 0:
            return True
        return False

    isempty = property(_isEmpty)

    def deleteSelected(self, delfiles=True, ifgone = False):
        filenames = [z[FILENAME] for z in self.selectedTags]
        #msg = '<br />'.join(filenames)
        if delfiles:
            result = QMessageBox.question (self, "puddletag",
                        "Are you sure you want to delete the selected files?",
                        "&Yes", "&No","", 1, 1)
        else:
            result = 0
        if result == 0:
            showmessage = True
            selectedRows = sorted(self.selectedRows)
            temprows = copy(selectedRows)
            for i,row in enumerate(selectedRows):
                try:
                    if ifgone:
                        if os.path.exists(filenames[i]):
                            continue
                    self.model().removeRows(temprows[i], msgparent = self, delfiles=delfiles)
                    temprows = [z - 1 for z in temprows]
                except (OSError, IOError), detail:
                    filename = self.rowTags(row)[FILENAME]
                    if len(selectedRows) > 1:
                        if showmessage:
                            errormsg = u"I couldn't delete <b>%s</b> (%s)<br /> Do you want to continue?" % \
                                        (filename, detail.strerror)
                            mb = QMessageBox('Error', errormsg, QMessageBox.Warning, QMessageBox.Yes or QMessageBox.Default,
                                    QMessageBox.No or QMessageBox.Escape, QMessageBox.YesAll, self )
                            ret = mb.exec_()
                            if ret == QMessageBox.No:
                                break
                            elif ret == QMessageBox.YesAll:
                                showmessage = False
                    else:
                        QMessageBox.critical(self,"Error", u"I couldn't delete the file <b>%s</b> (%s)" % \
                                            (filename, detail.strerror), QMessageBox.Ok, QMessageBox.NoButton)
            self.selectionChanged()

    def dragEnterEvent(self, event):
        event.accept()
        event.acceptProposedAction()

    def dropEvent(self, event):
        files = [unicode(z.path()) for z in event.mimeData().urls()]
        while '' in files:
            files.remove('')
        self.loadFiles(files, append = True)

    def dragMoveEvent(self, event):
        if event.source() == self:
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.accept()

    def mouseMoveEvent(self, event):

        if event.buttons() != Qt.LeftButton:
           return
        mimeData = QMimeData()
        plainText = ""
        tags= []
        if hasattr(self, "selectedRows"):
            selectedRows = self.selectedRows[::]
        else:
            return
        pnt = QPoint(*self.StartPosition)
        if (event.pos() - pnt).manhattanLength()  < QApplication.startDragDistance():
            return
        filenames = [z[FILENAME] for z in self.selectedTags]
        urls = [QUrl.fromLocalFile(f) for f in filenames]
        mimeData = QMimeData()
        mimeData.setUrls(urls)

        drag = QDrag(self)
        drag.setMimeData(mimeData)
        drag.setHotSpot(event.pos() - self.rect().topLeft())
        dropaction = drag.exec_()
        if dropaction == Qt.MoveAction:
            self.deleteSelected(False, True)

    def mousePressEvent(self, event):
        QTableView.mousePressEvent(self, event)
        if event.buttons()  == Qt.RightButton and self.model().taginfo:
            self.contextMenuEvent(event)
        if event.buttons() == Qt.LeftButton:
            self.StartPosition = [event.pos().x(), event.pos().y()]

    def fillTable(self, tags, append=False):
        """Clears the table and fills it with metadata in tags.

        tags are a list of audioinfo.Tag objects.
        If append is True then the tags are just appended.
        """
        self.selectedRows = []
        self.selectedColumns = []
        if append:
            self.saveSelection()
        self.model().load(tags, append = append)

        if append:
            self.restoreSelection()
        else:
            self.selectCorner()

        if self.autoresize:
            self.resizeColumnsToContents()
        if self.model().taginfo:
            self.emit(SIGNAL('viewfilled'), True)
        else:
            self.emit(SIGNAL('viewfilled'), False)


    def invertSelection(self):
        model = self.model()
        topLeft = model.index(0, 0);
        bottomRight = model.index(model.rowCount()-1, model.columnCount()-1)

        selection = QItemSelection(topLeft, bottomRight);
        self.selectionModel().select(selection, QItemSelectionModel.Toggle)

    def keyPressEvent(self, event):
        event.accept()
        #You might think that this is redundant since a delete
        #action is defined in contextMenuEvent, but if this isn't
        #done then the delegate is entered.
        if event.key() == Qt.Key_Delete and self.selectedRows:
            self.deleteSelected()
            return
        #This is so that an item isn't edited when the user's holding the shift or
        #control key.
        elif event.key() == Qt.Key_Space and (Qt.ControlModifier == event.modifiers() or Qt.ShiftModifier == event.modifiers()):
            trigger = self.editTriggers()
            self.setEditTriggers(self.NoEditTriggers)
            QTableView.keyPressEvent(self, event)
            self.setEditTriggers(trigger)
            return
        QTableView.keyPressEvent(self, event)

    def loadFiles(self, files = None, dirs = None, append = False, subfolders = None):
        assert files or dirs, 'Either files or dirs (or both) must be specified.'

        if subfolders is None:
            subfolders = self.subFolders

        if not files:
            files = []
        if not dirs:
            dirs = []
        elif isinstance(dirs, basestring):
            dirs = [dirs]

        if dirs:
            for d in dirs:
                files = [z for z in files if not z.startswith(d)]

        if self.dirs:
            for d in self.dirs:
                files = [z for z in files if not z.startswith(d)]

        if append:
            if subfolders:
                #Remove all subfolders if the parent's already loaded.
                for d in self.dirs:
                    dirs = [z for z in dirs if not z.startswith(d)]
                toremove = set()
                for d in dirs:
                    toremove = toremove.union([z for z in self.dirs if z.startswith(d)])
                self.removeFolders(toremove)
            else:
                self.removeFolders([z for z in dirs if z in self.dirs], False)
            self.dirs.extend(dirs)
        else:
            self.dirs = dirs
        files = set(files + getfiles(dirs, subfolders))

        tags = []
        finished = lambda: self._loadFilesDone(tags, append)
        def what():
            for f in files:
                tag = gettag(f)
                if tag is not None:
                    tags.append(tag)
                yield None

        s = progress(what, 'Loading ', len(files), finished)
        s(self.parentWidget())

    def _loadFilesDone(self, tags, append):
        self.fillTable(tags, append)
        self.emit(SIGNAL('dirschanged'), self.dirs)
        self.emit(SIGNAL('filesloaded'), True)
        if self._restore:
            self.clearSelection()
            self.restoreReloadSelection(*self._restore)
            self._restore = False

    def loadSettings(self):
        tags, checked = loadsettings()
        self.setHeaderTags([z for i, z in enumerate(tags) if i in checked])

    def playFiles(self):
        """Play the selected files using the player specified in self.playcommand"""
        if not self.selectedRows: return
        if hasattr(self, "playcommand"):
            if self.playcommand is True:
                li = [QUrl(path.join("file:///localhost", self.rowTags(z)[FILENAME])) for z in self.selectedRows]
                QDesktopServices.openUrl(li)
            else:
                li = copy(self.playcommand)
                li.extend([self.rowTags(z)[FILENAME] for z in self.selectedRows])
                try:
                    Popen(li)
                except (OSError), detail:
                    if detail.errno != 2:
                        QMessageBox.critical(self,"Error", u"I couldn't play the selected files: (<b>%s</b>) <br />Does the music player you defined (<b>%s</b>) exist?" % \
                                            (detail.strerror, u" ".join(self.playcommand)), QMessageBox.Ok, QMessageBox.NoButton)
                    else:
                        QMessageBox.critical(self,"Error", u"I couldn't play the selected files, because the music player you defined (<b>%s</b>) does not exist." \
                                            % u" ".join(self.playcommand), QMessageBox.Ok, QMessageBox.NoButton)

    def reloadFiles(self, filenames = None):
        self._restore = self.saveSelection()
        files = [z.filepath for z in self.model().taginfo if z[DIRPATH]
                        not in self.dirs]
        libfiles = [z for z in self.model().taginfo if '__library' in z]
        self.loadFiles(files, self.dirs, False, self.subFolders)
        self.model().load(libfiles, append = True)

    def rowTags(self,row, stringtags = False):
        """Returns all the tags pertinent to the file at row."""
        if stringtags:
            return self.model().taginfo[row].stringtags()
        return self.model().taginfo[row]

    def selectCurrentColumn(self):
        if self.selectedIndexes():
            col = self.selectedIndexes()[0].column()
            model = self.model()
            topLeft = model.index(0, col)
            bottomRight = model.index(model.rowCount()-1, col)

            selection = QItemSelection(topLeft, bottomRight);
            self.selectionModel().select(selection, QItemSelectionModel.Select)

    def selectAll(self):
        model = self.model()
        topLeft = model.index(0, 0);
        bottomRight = model.index(model.rowCount()-1, model.columnCount()-1)

        selection = QItemSelection(topLeft, bottomRight);
        self.selectionModel().select(selection, QItemSelectionModel.Select)


    def selectCorner(self):
        topLeft = self.model().index(0, 0)
        selection = QItemSelection(topLeft, topLeft)
        self.selectionModel().select(selection, QItemSelectionModel.Select)
        self.setFocus()

    def setModel(self, model):
        QTableView.setModel(self, model)
        #For less typing and that the model doesn't have to be accessed directly
        self.updateRow = model.setRowData
        self.connect(model, SIGNAL('modelReset'), self.selectionChanged)
        self.connect(model, SIGNAL('modelReset'), self.selectCorner)
        self.connect(model, SIGNAL('setDataError'), self.showTool)
        self.connect(model, SIGNAL('fileChanged()'), self.selectionChanged)

    def currentRowSelection(self):
        """Returns a dictionary with the currently selected rows as keys.
        Each key contains a list with the selected columns of that row.

        {} is returned if nothing is selected."""
        x = {}
        for z in self.selectedIndexes():
            try:
                x[z.row()].append(z.column())
            except KeyError:
                x[z.row()] = [z.column()]
        return x

    def currentColumnSelection(self):
        x = {}
        for z in self.selectedIndexes():
            try:
                x[z.column()].append(z.row())
            except KeyError:
                x[z.column()] = [z.row()]
        return x

    def _selectedTags(self):
        rowTags = self.rowTags
        return [rowTags(row) for row in self.selectedRows]

    selectedTags = property(_selectedTags)

    def _getSelectedRows(self):
        return self._selectedRows

    def _setSelectedRows(self, val):
        self._selectedRows = val

    selectedRows = property(_getSelectedRows, _setSelectedRows)

    def _getSelectedColumns(self):
        return self._selectedColumns

    def _setSelectedColumns(self, val):
        self._selectedColumns = val

    selectedColumns = property(_getSelectedColumns, _setSelectedColumns)

    def selectionChanged(self, selected = None, deselected = None):
        """Pretty important. This updates self.selectedRows, which is used
        everywhere.

        I've set selected an deselected as None, because I sometimes
        want self.selectedRows updated without hassle."""

        selectedRows = set()
        selectedColumns = set()
        for z in self.selectedIndexes():
            selectedRows.add(z.row())
            selectedColumns.add(z.column())
        self.selectedRows = sorted(list(selectedRows))
        self.selectedColumns = sorted(list(selectedColumns))

        if self.selectedRows:
            self.emit(SIGNAL('filesselected'), True)
        else:
            self.emit(SIGNAL('filesselected'), False)

        if selected is not None and deselected is not None:
            QTableView.selectionChanged(self, selected, deselected)
        self.emit(SIGNAL('itemSelectionChanged()'))
        tags =  [z for i,z in enumerate(self.model().taginfo) if i in selectedRows]
        self.emit(SIGNAL('tagselectionchanged'), tags, selectedRows, selectedColumns)

    def saveSelection(self):
        self._currentcol = self.currentColumnSelection()
        self._currentrow = self.currentRowSelection()
        self._currenttags = [(row, self.rowTags(row)) for row in self._currentrow]
        return (self._currentrow, self._currentcol, self._currenttags)

    def restoreReloadSelection(self, currentrow, currentcol, tags):
        if not tags:
            return

        def getGroups(rows):
            groups = []
            try:
                last = [rows[0]]
            except IndexError:
                return []
            for row in rows[1:]:
                if row - 1 == last[-1]:
                    last.append(row)
                else:
                    groups.append(last)
                    last = [row]
            groups.append(last)
            return groups

        modelindex = self.model().index
        filenames = [z[FILENAME] for z in self.model().taginfo]
        getrow = lambda x: filenames.index(x[FILENAME])
        selection = QItemSelection()
        select = lambda top, low, col: selection.append(
                        QItemSelectionRange(modelindex(top, col),
                                                    modelindex(low, col)))

        newindexes = {}
        while True:
            try:
                tag = tags[0]
            except IndexError:
                break
            try:
                newindexes[tag[0]] = getrow(tag[1])
            except ValueError:
                pass
            del(tags[0])

        groups = {}
        for col, rows in currentcol.items():
            groups[col] = getGroups(sorted([newindexes[row] for row in rows if row in newindexes]))

        for col, rows in groups.items():
            [select(min(row), max(row), col) for row in rows]
        self.selectionModel().select(selection, QItemSelectionModel.Select)

    def restoreSelection(self, currentrow=None, currentcol=None, tags=None):
        if not currentrow:
            currentrow = self._currentrow
            currentcol = self._currentcol
            tags = self._currenttags
        if not tags:
            return

        def getGroups(rows):
            groups = []
            try:
                last = [rows[0]]
            except IndexError:
                return []
            for row in rows[1:]:
                if row - 1 == last[-1]:
                    last.append(row)
                else:
                    groups.append(last)
                    last = [row]
            groups.append(last)
            return groups

        getrow = self.model().taginfo.index
        modelindex = self.model().index
        selection = QItemSelection()
        select = lambda top, low, col: selection.append(
                        QItemSelectionRange(modelindex(top, col),
                                                    modelindex(low, col)))

        newindexes = {}

        while True:
            try:
                tag = tags[0]
            except IndexError:
                break
            newindexes[tag[0]] = getrow(tag[1])
            del(tags[0])
        groups = {}
        for col, rows in currentcol.items():
            groups[col] = getGroups(sorted([newindexes[row] for row in rows if row in newindexes]))

        for col, rows in groups.items():
            [select(min(row), max(row), col) for row in rows]
        self.selectionModel().select(selection, QItemSelectionModel.Select)

    def removeFolders(self, dirs, valid = True):
        if dirs:
            self.dirs = list(set(self.dirs).difference(dirs))
            self.model().removeFolders(dirs, valid)

    def setHeaderTags(self, tags):
        self.model().setHeader(tags)

    def setHorizontalHeader(self, header):
        QTableView.setHorizontalHeader(self, header)
        self.connect(header, SIGNAL('saveSelection'), self.saveSelection)

    def showTool(self, row, column, text):
        """Shows a tooltip when an error occors.

        Actually, a tooltip is never shown, because the table
        is updated as soon as it tries to show it. So a setDataError
        signal is emitted with the text that can be used to show
        text in the status bar or something."""
        y = -self.mapFromGlobal(self.pos()).y() + self.rowViewportPosition(row)
        x = -self.mapFromGlobal(self.pos()).x() + self.columnViewportPosition(column)
        QToolTip.showText(QPoint(x,y), text)
        self.emit(SIGNAL('setDataError'), text)

    def showProperties(self):
        f = self.selectedTags[0]
        win = Properties(f.info, self.parentWidget())
        win.show()

    def setPlayCommand(self, command):
        self.playcommand = command


    def sortByColumn(self, column):
        """Guess"""
        QTableView.sortByColumn(self, column)
        self.restoreSelection()

    def wheelEvent(self, e):
        h = self.horizontalScrollBar()
        if not self.verticalScrollBar().isVisible() and h.isVisible():
            numsteps = e.delta() / 5
            h.setValue(h.value() + numsteps)
            e.accept()
        else:
            QTableView.wheelEvent(self, e)