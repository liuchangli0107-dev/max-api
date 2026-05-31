import logging
import os
import time
from logging.handlers import BaseRotatingHandler

class TimedSizeRotatingFileHandler(BaseRotatingHandler):
    def __init__(self, filename, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'):
        self.maxBytes = maxBytes
        self.backupCount = backupCount
        super().__init__(filename, 'a', encoding=encoding)

    def shouldRollover(self, record):
        if self.stream is None:
            return False
        if self.maxBytes > 0:
            self.stream.seek(0, 2)
            if self.stream.tell() >= self.maxBytes:
                return True
        return False

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        
        # 產生格式為 grid_state_live_YYYYMMDDHHMMSS.log 的檔名
        timestamp = time.strftime("%Y%m%d%H%M%S")
        root, ext = os.path.splitext(self.baseFilename)
        new_filename = f"{root}_{timestamp}{ext}"
        
        if os.path.exists(new_filename):
            os.remove(new_filename)
        os.rename(self.baseFilename, new_filename)
        
        # 刪除過舊的備份 (這裡簡單刪除最舊的)
        files = [f for f in os.listdir(os.path.dirname(self.baseFilename)) 
                 if f.startswith(os.path.basename(root)) and f.endswith(ext)]
        files.sort()
        if len(files) > self.backupCount:
            for i in range(len(files) - self.backupCount):
                os.remove(os.path.join(os.path.dirname(self.baseFilename), files[i]))
        
        self.stream = self._open()
