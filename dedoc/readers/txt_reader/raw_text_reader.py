from typing import Iterable, List, Optional, Tuple

from dedoc.data_structures.concrete_annotations.indentation_annotation import IndentationAnnotation
from dedoc.data_structures.line_with_meta import LineWithMeta
from dedoc.data_structures.unstructured_document import UnstructuredDocument
from dedoc.readers.base_reader import BaseReader


class RawTextReader(BaseReader):
    """
    This class allows to parse files with the following extensions: .txt, .txt.gz
    """

    def __init__(self, *, config: Optional[dict] = None) -> None:
        import re
        from dedoc.extensions import recognized_extensions, recognized_mimes

        super().__init__(config=config, recognized_extensions=recognized_extensions.txt_like_format, recognized_mimes=recognized_mimes.txt_like_format)
        self.space_regexp = re.compile(r"^\s+")

    def can_read(self, file_path: Optional[str] = None, mime: Optional[str] = None, extension: Optional[str] = None, parameters: Optional[dict] = None) -> bool:
        """
        Check if the document extension is suitable for this reader.
        Look to the documentation of :meth:`~dedoc.readers.BaseReader.can_read` to get information about the method's parameters.
        """
        from dedoc.utils.utils import get_mime_extension

        mime, extension = get_mime_extension(file_path=file_path, mime=mime, extension=extension)
        # this code differs from BaseReader because other formats can have text/plain mime type
        if extension:
            return extension.lower() in self._recognized_extensions
        return mime in self._recognized_mimes

    def read(self, file_path: str, parameters: Optional[dict] = None) -> UnstructuredDocument:
        """
        This method returns only document lines.
        Look to the documentation of :meth:`~dedoc.readers.BaseReader.read` to get information about the method's parameters.
        """
        parameters = {} if parameters is None else parameters
        encoding = self.__get_encoding(path=file_path, parameters=parameters)
        lines = self._get_lines_with_meta(path=file_path, encoding=encoding)
        encoding_warning = f"encoding is {encoding}"
        result = UnstructuredDocument(lines=lines, tables=[], attachments=[], warnings=[encoding_warning])
        return self._postprocess(result)

    def __get_encoding(self, path: str, parameters: dict) -> str:
        from dedoc.utils.utils import get_encoding

        if parameters.get("encoding"):
            return parameters["encoding"]
        else:
            return get_encoding(path, "utf-8")

    def _get_lines_with_meta(self, path: str, encoding: str) -> List[LineWithMeta]:
        import time
        from dedoc.data_structures.concrete_annotations.spacing_annotation import SpacingAnnotation
        from dedoc.data_structures.hierarchy_level import HierarchyLevel
        from dedoc.data_structures.line_metadata import LineMetadata
        from dedoc.utils.utils import calculate_file_hash

        lines = []
        file_hash = calculate_file_hash(path=path)
        number_of_empty_lines = 0
        previous_log_time = time.time()

        for line_id, line in self.__get_lines(path=path, encoding=encoding):
            if time.time() - previous_log_time > 5:
                self.logger.info(f"done {line_id} lines")
                previous_log_time = time.time()

            metadata = LineMetadata(page_id=0, line_id=line_id)
            uid = f"txt_{file_hash}_{line_id}"
            spacing_annotation_value = str(int(100 * (0.5 if number_of_empty_lines == 0 else number_of_empty_lines)))
            spacing_annotation = SpacingAnnotation(start=0, end=len(line), value=spacing_annotation_value)
            indent_annotation = self.__get_indent_annotation(line)

            line_with_meta = LineWithMeta(line=line, metadata=metadata, annotations=[spacing_annotation, indent_annotation], uid=uid)
            line_with_meta.metadata.tag_hierarchy_level = HierarchyLevel.create_unknown()
            lines.append(line_with_meta)

            number_of_empty_lines = number_of_empty_lines + 1 if line.isspace() else 0

        return lines

    def __get_lines(self, path: str, encoding: str) -> Iterable[Tuple[int, str]]:
        import codecs
        import gzip
        from unicodedata import normalize

        if path.lower().endswith("txt"):
            with codecs.open(path, errors="ignore", encoding=encoding) as file:
                for line_id, line in enumerate(file):
                    line = normalize("NFC", line).replace("й", "й")  # й replace matter
                    yield line_id, line
        else:
            with gzip.open(path) as file:
                for line_id, line in enumerate(file):
                    line = line.decode(encoding)
                    line = normalize("NFC", line).replace("й", "й")
                    yield line_id, line

    def __get_starting_spacing(self, line: Optional[LineWithMeta]) -> int:
        if line is None or line.line.isspace():
            return 0
        space_this = self.space_regexp.match(line.line.replace("\t", " " * 4))
        if space_this is None:
            return 0
        return space_this.end() - space_this.start()

    def __is_paragraph(self, line: LineWithMeta, previous_line: Optional[LineWithMeta]) -> bool:
        space_this = self.__get_starting_spacing(line)
        space_prev = self.__get_starting_spacing(previous_line)
        return not line.line.isspace() and space_this - space_prev >= 2

    def _postprocess(self, document: UnstructuredDocument) -> UnstructuredDocument:
        previous_line = None
        for line in document.lines:
            is_paragraph = self.__is_paragraph(line=line, previous_line=previous_line)
            line.metadata.tag_hierarchy_level.can_be_multiline = not is_paragraph
            previous_line = line
        return document

    def __get_indent_annotation(self, line: str) -> IndentationAnnotation:
        space_group = self.space_regexp.match(line)
        if space_group is None:
            return IndentationAnnotation(start=0, end=len(line), value="0")
        space_cnt = 0
        for char in space_group.group():
            space_cnt += 3 if char == "\t" else 1
        return IndentationAnnotation(start=0, end=len(line), value=str(211 * space_cnt))
