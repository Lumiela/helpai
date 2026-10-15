"""Import before cv2 / ultralytics.

Some environments (e.g. the training server) have a mixed opencv install where importing cv2.typing raises
AttributeError and breaks "import ultralytics". Blocking that optional submodule is harmless everywhere else.
"""
import importlib.abc
import sys


class _BlockCv2Typing(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "cv2.typing":
            raise ImportError("cv2.typing skipped")
        return None


if not any(type(f).__name__ == "_BlockCv2Typing" for f in sys.meta_path):
    sys.meta_path.insert(0, _BlockCv2Typing())
