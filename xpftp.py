import sys
import os
import json
import stat
import time
import shutil
import paramiko

from PyQt4 import QtGui, QtCore

PROFILE_FILE = "profiles.json"

# ---------------- Utilities ----------------
def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return '{0:.1f} {1}'.format(size, unit)
        size /= 1024.0
    return '{0:.1f} PB'.format(size)

# ---------------- SFTP Client ----------------
class SFTPClient(object):
    def __init__(self):
        self.transport = None
        self.sftp = None

    def connect(self, host, port, username, password):
        self.transport = paramiko.Transport((host, port))
        self.transport.connect(username=username, password=password)
        self.sftp = paramiko.SFTPClient.from_transport(self.transport)

    def listdir_attr(self, path):
        return self.sftp.listdir_attr(path)

    def is_dir(self, path):
        try:
            mode = self.sftp.stat(path).st_mode
            return stat.S_ISDIR(mode)
        except:
            return False

    def upload(self, local, remote, progress_callback=None):
        if os.path.isdir(local):
            self._upload_dir(local, remote, progress_callback)
        else:
            self.sftp.put(local, remote, callback=progress_callback)

    def _upload_dir(self, local_dir, remote_dir, progress_callback=None):
        try:
            self.sftp.stat(remote_dir)
        except IOError:
            self.sftp.mkdir(remote_dir)
        for item in os.listdir(local_dir):
            lp = os.path.join(local_dir, item)
            rp = remote_dir + '/' + item
            if os.path.isdir(lp):
                self._upload_dir(lp, rp, progress_callback)
            else:
                self.sftp.put(lp, rp, callback=progress_callback)

    def download(self, remote, local, progress_callback=None):
        if self.is_dir(remote):
            self._download_dir(remote, local, progress_callback)
        else:
            self.sftp.get(remote, local, callback=progress_callback)

    def _download_dir(self, remote_dir, local_dir, progress_callback=None):
        if not os.path.exists(local_dir):
            os.makedirs(local_dir)
        try:
            items = self.sftp.listdir_attr(remote_dir)
        except:
            return
        for item in items:
            rp = remote_dir + '/' + item.filename
            lp = os.path.join(local_dir, item.filename)
            if stat.S_ISDIR(item.st_mode):
                self._download_dir(rp, lp, progress_callback)
            else:
                try:
                    self.sftp.get(rp, lp, callback=progress_callback)
                except:
                    pass

# ---------------- Profiles ----------------
def load_profiles():
    if not os.path.exists(PROFILE_FILE):
        return {}
    with open(PROFILE_FILE, 'r') as f:
        return json.load(f)

def save_profiles(profiles):
    with open(PROFILE_FILE, 'w') as f:
        json.dump(profiles, f, indent=4)

# ---------------- Drag-and-Drop Trees ----------------
class LocalTree(QtGui.QTreeWidget):
    def __init__(self, parent=None):
        super(LocalTree, self).__init__(parent)
        self.parent_window = parent
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDefaultDropAction(QtCore.Qt.CopyAction)

    def startDrag(self, dropActions):
        item = self.currentItem()
        if item and item.text(0) != '..':
            path = os.path.join(self.parent_window.local_path, item.text(0))
            mime = QtCore.QMimeData()
            mime.setText(path)
            drag = QtGui.QDrag(self)
            drag.setMimeData(mime)
            drag.exec_(QtCore.Qt.CopyAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        source_path = str(event.mimeData().text())
        local_dest = os.path.join(self.parent_window.local_path, os.path.basename(source_path))
        if os.path.abspath(source_path) == os.path.abspath(local_dest):
            event.ignore()
            return
        try:
            self.parent_window.transfer_with_progress(self.parent_window.sftp.download, source_path, local_dest)
        except Exception as e:
            QtGui.QMessageBox.warning(self, "Download Error", str(e))
        self.parent_window.refresh_local()
        event.acceptProposedAction()

class RemoteTree(QtGui.QTreeWidget):
    def __init__(self, parent=None):
        super(RemoteTree, self).__init__(parent)
        self.parent_window = parent
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDefaultDropAction(QtCore.Qt.CopyAction)

    def startDrag(self, dropActions):
        item = self.currentItem()
        if item and item.text(0) != '..':
            path = self.parent_window.remote_path + '/' + item.text(0)
            mime = QtCore.QMimeData()
            mime.setText(path)
            drag = QtGui.QDrag(self)
            drag.setMimeData(mime)
            drag.exec_(QtCore.Qt.CopyAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        source_path = str(event.mimeData().text())
        remote_dest = self.parent_window.remote_path + '/' + os.path.basename(source_path)
        if source_path == remote_dest:
            event.ignore()
            return
        try:
            self.parent_window.transfer_with_progress(self.parent_window.sftp.upload, source_path, remote_dest)
        except Exception as e:
            QtGui.QMessageBox.warning(self, "Upload Error", str(e))
        self.parent_window.refresh_remote()
        event.acceptProposedAction()

# ---------------- Main Window ----------------
class MainWindow(QtGui.QWidget):
    def __init__(self):
        super(MainWindow, self).__init__()
        self.setWindowTitle('SFTP Client for Windows XP :D')
        self.resize(1000, 600)

        self.sftp = SFTPClient()
        self.profiles = load_profiles()
        self.local_path = os.path.expanduser("~")
        self.remote_path = "."
        self.show_hidden = False
        self.connected = False

        layout = QtGui.QVBoxLayout(self)

        # Top controls
        top_layout = QtGui.QHBoxLayout()
        self.profile_box = QtGui.QComboBox()
        self.profile_box.addItems(list(self.profiles.keys()))
        self.host_edit = QtGui.QLineEdit()
        self.user_edit = QtGui.QLineEdit()
        self.pass_edit = QtGui.QLineEdit()
        self.pass_edit.setEchoMode(QtGui.QLineEdit.Password)
        self.connect_btn = QtGui.QPushButton('Connect')
        self.save_profile_btn = QtGui.QPushButton('Save Profile')

        top_layout.addWidget(QtGui.QLabel('Profile'))
        top_layout.addWidget(self.profile_box)
        top_layout.addWidget(QtGui.QLabel('Host'))
        top_layout.addWidget(self.host_edit)
        top_layout.addWidget(QtGui.QLabel('User'))
        top_layout.addWidget(self.user_edit)
        top_layout.addWidget(QtGui.QLabel('Password'))
        top_layout.addWidget(self.pass_edit)
        top_layout.addWidget(self.connect_btn)
        top_layout.addWidget(self.save_profile_btn)

        layout.addLayout(top_layout)

        # hidden + refresh icon
        bottom_top_layout = QtGui.QHBoxLayout()
        self.show_hidden_cb = QtGui.QCheckBox("Show hidden files")
        self.refresh_btn = QtGui.QPushButton()
        self.refresh_btn.setIcon(self.style().standardIcon(QtGui.QStyle.SP_BrowserReload))
        self.refresh_btn.setToolTip("Refresh files")
        bottom_top_layout.addWidget(self.show_hidden_cb)
        bottom_top_layout.addWidget(self.refresh_btn)
        bottom_top_layout.addStretch()
        layout.addLayout(bottom_top_layout)

        # File trees
        files_layout = QtGui.QHBoxLayout()
        self.local_tree = LocalTree(self)
        self.remote_tree = RemoteTree(self)
        for tree in (self.local_tree, self.remote_tree):
            tree.setColumnCount(3)
            tree.setHeaderLabels(['Name', 'Size', 'Modified'])
            tree.setRootIsDecorated(False)
            tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            tree.customContextMenuRequested.connect(self.show_context_menu)
        files_layout.addWidget(self.local_tree)
        files_layout.addWidget(self.remote_tree)
        layout.addLayout(files_layout)

        # Signals
        self.connect_btn.clicked.connect(self.connect_sftp)
        self.save_profile_btn.clicked.connect(self.save_profile)
        self.refresh_btn.clicked.connect(self.refresh_all)
        self.profile_box.currentIndexChanged.connect(lambda idx: self.load_profile(self.profile_box.currentText()))
        self.show_hidden_cb.stateChanged.connect(self.toggle_show_hidden)
        self.local_tree.itemDoubleClicked.connect(self.local_item_double)
        self.remote_tree.itemDoubleClicked.connect(self.remote_item_double)

        self.load_profile(self.profile_box.currentText())
        self.refresh_local()

    # ---------- Profiles ----------
    def load_profile(self, name):
        if name in self.profiles:
            p = self.profiles[name]
            self.host_edit.setText(p.get('host', ''))
            self.user_edit.setText(p.get('username', ''))
            self.pass_edit.setText(p.get('password', ''))

    def save_profile(self):
        name, ok = QtGui.QInputDialog.getText(self, 'Save Profile', 'Enter profile name:')
        if ok and name:
            self.profiles[str(name)] = {
                'host': self.host_edit.text(),
                'username': self.user_edit.text(),
                'password': self.pass_edit.text()
            }
            save_profiles(self.profiles)
            self.profile_box.clear()
            self.profile_box.addItems(self.profiles.keys())
            self.profile_box.setCurrentIndex(self.profile_box.findText(name))

    # ---------- Connection ----------
    def connect_sftp(self):
        try:
            self.sftp.connect(
                self.host_edit.text(),
                22,
                self.user_edit.text(),
                self.pass_edit.text()
            )
            self.connected = True
            QtGui.QMessageBox.information(self, "Connected", "SFTP connection successful.")
            self.remote_path = "."
            self.refresh_remote()
        except Exception as e:
            QtGui.QMessageBox.critical(self, "Connection Error", str(e))
            self.connected = False

    # ---------- Refresh ----------
    def refresh_all(self):
        self.refresh_local()
        if self.connected:
            self.refresh_remote()

    def refresh_local(self):
        self.local_tree.clear()
        parent_dir = os.path.dirname(self.local_path)
        if parent_dir != self.local_path:
            up_item = QtGui.QTreeWidgetItem(['..', '', ''])
            up_item.setIcon(0, self.style().standardIcon(QtGui.QStyle.SP_DirIcon))
            self.local_tree.addTopLevelItem(up_item)

        try:
            entries = os.listdir(self.local_path)
            if not self.show_hidden:
                entries = [e for e in entries if not e.startswith('.')]
            entries = sorted(entries, key=lambda e: (not os.path.isdir(os.path.join(self.local_path, e)), e.lower()))
            for f in entries:
                full_path = os.path.join(self.local_path, f)
                size = format_size(os.path.getsize(full_path)) if os.path.isfile(full_path) else ''
                mtime = time.ctime(os.path.getmtime(full_path))
                item = QtGui.QTreeWidgetItem([f, size, mtime])
                icon = self.style().standardIcon(QtGui.QStyle.SP_DirIcon) if os.path.isdir(full_path) else self.style().standardIcon(QtGui.QStyle.SP_FileIcon)
                item.setIcon(0, icon)
                self.local_tree.addTopLevelItem(item)
        except Exception as e:
            QtGui.QMessageBox.warning(self, "Error", "Cannot access local path: " + str(e))

    def refresh_remote(self):
        if not self.connected:
            return
        self.remote_tree.clear()
        if self.remote_path not in ['.', '/']:
            up_item = QtGui.QTreeWidgetItem(['..', '', ''])
            up_item.setIcon(0, self.style().standardIcon(QtGui.QStyle.SP_DirIcon))
            self.remote_tree.addTopLevelItem(up_item)

        try:
            items = self.sftp.listdir_attr(self.remote_path)
            if not self.show_hidden:
                items = [i for i in items if not i.filename.startswith('.')]
            items = sorted(items, key=lambda f: (not stat.S_ISDIR(f.st_mode), f.filename.lower()))
            for f in items:
                size = format_size(f.st_size) if not stat.S_ISDIR(f.st_mode) else ''
                mtime = time.ctime(f.st_mtime)
                item = QtGui.QTreeWidgetItem([f.filename, size, mtime])
                icon = self.style().standardIcon(QtGui.QStyle.SP_DirIcon) if stat.S_ISDIR(f.st_mode) else self.style().standardIcon(QtGui.QStyle.SP_FileIcon)
                item.setIcon(0, icon)
                self.remote_tree.addTopLevelItem(item)
        except Exception as e:
            QtGui.QMessageBox.warning(self, "Error", "Cannot access remote path: " + str(e))

    # ---------- Navigation ----------
    def local_item_double(self, item, column):
        name = str(item.text(0))
        if name == '..':
            parent = os.path.dirname(self.local_path)
            if os.path.exists(parent):
                self.local_path = parent
                self.refresh_local()
            return
        new_path = os.path.join(self.local_path, name)
        if os.path.isdir(new_path):
            self.local_path = new_path
            self.refresh_local()

    def remote_item_double(self, item, column):
        name = str(item.text(0))
        if name == '..':
            if self.remote_path not in ['.', '/']:
                parent = os.path.dirname(self.remote_path)
                self.remote_path = parent if parent else '.'
                self.refresh_remote()
            return
        new_path = self.remote_path + '/' + name
        if self.sftp.is_dir(new_path):
            self.remote_path = new_path
            self.refresh_remote()

    # ---------- Show Hidden Files ----------
    def toggle_show_hidden(self, state):
        self.show_hidden = (state == QtCore.Qt.Checked)
        self.refresh_all()

    # ---------- Delete Key ----------
    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Delete:
            if self.local_tree.hasFocus():
                item = self.local_tree.currentItem()
                if item:
                    self.delete_item(self.local_tree, item)
            elif self.remote_tree.hasFocus():
                item = self.remote_tree.currentItem()
                if item:
                    self.delete_item(self.remote_tree, item)

    # ---------- Context Menu ----------
    def show_context_menu(self, pos):
        tree = self.sender()
        item = tree.itemAt(pos)
        menu = QtGui.QMenu()

        if item and item.text(0) != '..':
            delete_action = menu.addAction("Delete")
            rename_action = menu.addAction("Rename")
            if tree == self.remote_tree:
                download_action = menu.addAction("Download")
            else:
                upload_action = menu.addAction("Upload")
        else:
            create_folder_action = menu.addAction("Create Folder")

        action = menu.exec_(tree.mapToGlobal(pos))

        if item and item.text(0) != '..':
            if action == delete_action:
                self.delete_item(tree, item)
            elif action == rename_action:
                self.rename_item(tree, item)
            elif tree == self.remote_tree and action == download_action:
                self.download_item(item)
            elif tree == self.local_tree and action == upload_action:
                self.upload_item(item)
        else:
            if not item and action == create_folder_action:
                self.create_folder(tree)

    # ---------- File Operations ----------
    def delete_item(self, tree, item):
        name = str(item.text(0))
        if tree == self.local_tree:
            path = os.path.join(self.local_path, name)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception as e:
                QtGui.QMessageBox.warning(self, "Error", str(e))
            self.refresh_local()
        else:
            path = self.remote_path + '/' + name
            try:
                if self.sftp.is_dir(path):
                    for f in self.sftp.listdir_attr(path):
                        self.sftp.download(path + '/' + f.filename, os.path.join(self.local_path, f.filename))
                    self.sftp.sftp.rmdir(path)
                else:
                    self.sftp.sftp.remove(path)
            except Exception as e:
                QtGui.QMessageBox.warning(self, "Error", str(e))
            self.refresh_remote()

    def rename_item(self, tree, item):
        old_name = str(item.text(0))
        new_name, ok = QtGui.QInputDialog.getText(self, "Rename", "New name:", text=old_name)
        if not ok or not new_name or new_name == old_name:
            return
        if tree == self.local_tree:
            old_path = os.path.join(self.local_path, old_name)
            new_path = os.path.join(self.local_path, new_name)
            try:
                os.rename(old_path, new_path)
            except Exception as e:
                QtGui.QMessageBox.warning(self, "Rename Error", str(e))
            self.refresh_local()
        else:
            old_path = self.remote_path + '/' + old_name
            new_path = self.remote_path + '/' + new_name
            try:
                self.sftp.sftp.rename(old_path, new_path)
            except Exception as e:
                QtGui.QMessageBox.warning(self, "Rename Error", str(e))
            self.refresh_remote()

    def create_folder(self, tree):
        name, ok = QtGui.QInputDialog.getText(self, "Create Folder", "Folder name:")
        if not ok or not name:
            return
        if tree == self.local_tree:
            path = os.path.join(self.local_path, name)
            try:
                os.makedirs(path)
            except Exception as e:
                QtGui.QMessageBox.warning(self, "Error", str(e))
            self.refresh_local()
        else:
            path = self.remote_path + '/' + name
            try:
                self.sftp.sftp.mkdir(path)
            except Exception as e:
                QtGui.QMessageBox.warning(self, "Error", str(e))
            self.refresh_remote()

    # ---------- Upload/Download helpers ----------
    def transfer_with_progress(self, func, src, dst):
        progress = QtGui.QProgressDialog("Transferring...", "Cancel", 0, 100, self)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.show()

        def callback(transferred, total):
            if total > 0:
                percent = int(transferred * 100 / total)
                progress.setValue(percent)
                QtGui.QApplication.processEvents()

        try:
            func(src, dst, progress_callback=callback)
        finally:
            progress.setValue(100)

    def upload_item(self, item):
        path = os.path.join(self.local_path, item.text(0))
        remote_path = self.remote_path + '/' + item.text(0)
        self.transfer_with_progress(self.sftp.upload, path, remote_path)
        self.refresh_remote()

    def download_item(self, item):
        remote_path = self.remote_path + '/' + item.text(0)
        local_path = os.path.join(self.local_path, item.text(0))
        self.transfer_with_progress(self.sftp.download, remote_path, local_path)
        self.refresh_local()

# ---------------- Run ----------------
if __name__ == '__main__':
    app = QtGui.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
