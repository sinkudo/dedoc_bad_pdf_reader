import binascii
import itertools
import logging
import os
import uuid
from collections import namedtuple
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image
from dedocutils.data_structures import BBox
from numpy import ndarray
from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams, LTAnno, LTChar, LTContainer, LTCurve, LTFigure, LTImage, LTRect
from pdfminer.layout import LTTextBox, LTTextBoxHorizontal, LTTextContainer, LTTextLineHorizontal
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage

from dedoc.data_structures.annotation import Annotation
from dedoc.data_structures.concrete_annotations.bbox_annotation import BBoxAnnotation
from dedoc.data_structures.concrete_annotations.bold_annotation import BoldAnnotation
from dedoc.data_structures.concrete_annotations.italic_annotation import ItalicAnnotation
from dedoc.data_structures.concrete_annotations.size_annotation import SizeAnnotation
from dedoc.data_structures.concrete_annotations.style_annotation import StyleAnnotation
from dedoc.data_structures.unstructured_document import UnstructuredDocument
from dedoc.readers.pdf_reader.data_classes.line_with_location import LineWithLocation
from dedoc.readers.pdf_reader.data_classes.page_with_bboxes import PageWithBBox
from dedoc.readers.pdf_reader.data_classes.pdf_image_attachment import PdfImageAttachment
from dedoc.readers.pdf_reader.data_classes.tables.location import Location
from dedoc.readers.pdf_reader.data_classes.tables.scantable import ScanTable
from dedoc.readers.pdf_reader.data_classes.text_with_bbox import TextWithBBox
from dedoc.readers.pdf_reader.data_classes.word_with_bbox import WordWithBBox
from dedoc.readers.pdf_reader.pdf_base_reader import ParametersForParseDoc
from dedoc.readers.pdf_reader.pdf_base_reader import PdfBaseReader
from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdfminer_reader.pdfminer_utils import create_bbox, draw_annotation
from dedoc.utils.parameter_utils import get_path_param
from dedoc.utils.pdf_utils import get_page_image

#########################################
logging.getLogger("pdfminer").setLevel(logging.ERROR)
WordObj = namedtuple("Word", ["start", "end", "value"])

# my_proj_path = "D:\\laboratory-repository\\text-extraction\\pdf-complex-background-text-extraction"
# sys.path.insert(0, my_proj_path)

# from pdf_worker.pdf_reader import PDFReader
from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdf_broken_encoding_reader.pdf_worker.pdf_reader import PDFReader

class PdfBrokenEncodingReader(PdfBaseReader):
    """
    This class allows to extract content (text, tables, attachments) from the .pdf documents with a textual layer (copyable documents).
    It uses a pdfminer library for content extraction.

    For more information, look to `pdf_with_text_layer` option description in :ref:`pdf_handling_parameters`.
    """

    def __init__(self, *, config: Optional[dict] = None) -> None:
        from dedoc.extensions import recognized_extensions, recognized_mimes

        super().__init__(config=config, recognized_extensions=recognized_extensions.pdf_like_format,
                         recognized_mimes=recognized_mimes.pdf_like_format)

        from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdfminer_reader.pdfminer_extractor import PdfminerExtractor
        self.extractor_layer = PdfminerExtractor(config=self.config)
        self.fonts = []

    def can_read(self, file_path: Optional[str] = None, mime: Optional[str] = None, extension: Optional[str] = None,
                 parameters: Optional[dict] = None) -> bool:
        """
        Check if the document extension is suitable for this reader (PDF format is supported only).
        This method returns `True` only when the key `pdf_with_text_layer` with value `true` is set in the dictionary `parameters`.

        You can look to :ref:`pdf_handling_parameters` to get more information about `parameters` dictionary possible arguments.

        Look to the documentation of :meth:`~dedoc.readers.BaseReader.can_read` to get information about the method's parameters.
        """
        from dedoc.utils.parameter_utils import get_param_pdf_with_txt_layer
        return super().can_read(file_path=file_path, mime=mime, extension=extension) and get_param_pdf_with_txt_layer(
            parameters) == "true"

    def read(self, file_path: str, parameters: Optional[dict] = None) -> UnstructuredDocument:

        import dedoc.utils.parameter_utils as param_utils
        parameters = {} if parameters is None else parameters
        first_page, last_page = param_utils.get_param_page_slice(parameters)
        params_for_parse = ParametersForParseDoc(
            language=param_utils.get_param_language(parameters),
            is_one_column_document=param_utils.get_param_is_one_column_document(parameters),
            document_orientation=param_utils.get_param_document_orientation(parameters),
            need_header_footers_analysis=param_utils.get_param_need_header_footers_analysis(parameters),
            need_pdf_table_analysis=param_utils.get_param_need_pdf_table_analysis(parameters),
            first_page=first_page,
            last_page=last_page,
            need_binarization=param_utils.get_param_need_binarization(parameters),
            table_type=param_utils.get_param_table_type(parameters),
            with_attachments=param_utils.get_param_with_attachments(parameters),
            attachments_dir=param_utils.get_param_attachments_dir(parameters, file_path),
            need_content_analysis=param_utils.get_param_need_content_analysis(parameters),
            need_gost_frame_analysis=param_utils.get_param_need_gost_frame_analysis(parameters),
            pdf_with_txt_layer=param_utils.get_param_pdf_with_txt_layer(parameters)
        )

        # unstructured_document = super().read(file_path, parameters)
        reader = PDFReader.load_default_model("ruseng")
        pages, layouts = reader.get_correct_layout(file_path)
        tables = []
        lines = []
        for idx, (page, layout) in enumerate(zip(pages, layouts)):
            page_bb = self.__handle_page(page, idx, file_path, params_for_parse, layout)
            unreadable_blocks = [location.bbox for table in tables for location in table.locations]
            page_bb.bboxes = [bbox for bbox in page_bb.bboxes if
                              not self._inside_any_unreadable_block(bbox.bbox, unreadable_blocks)]
            # lines = self.metadata_extractor.extract_metadata_and_set_annotations(page_with_lines=page_bb, call_classifier=False)
            lines += self.metadata_extractor.extract_metadata_and_set_annotations(page_with_lines=page_bb,
                                                                                  call_classifier=False)

        return UnstructuredDocument(tables=[], lines=lines, attachments=[])

    def _process_one_page(self,
                          image: ndarray,
                          parameters: ParametersForParseDoc,
                          page_number: int,
                          path: str) -> Tuple[
        List[LineWithLocation], List[ScanTable], List[PdfImageAttachment], List[float]]:
        if parameters.need_pdf_table_analysis:
            gray_image = self._convert_to_gray(image)
            cleaned_image, tables = self.table_recognizer.recognize_tables_from_image(
                image=gray_image,
                page_number=page_number,
                language=parameters.language,
                table_type=parameters.table_type
            )
        else:
            tables = []

        # lines = self.metadata_extractor.extract_metadata_and_set_annotations(page_with_lines=page, call_classifier=False)
        reader = PDFReader.load_default_model("ruseng")
        ## получаю layouts от своего (в layouts pages)
        layout = reader.get_correct_layout(path)
        # lines = LineWithLocation(line='CHECK', annotations=None, metadata=None, location=Location(None,None))

        lines = []
        # в цикле из pages с помощью metadata_extractor.py достаю bbox
        for idx, page in enumerate(layout):
            page_bb = self.__handle_page(page, idx, path, parameters)
            unreadable_blocks = [location.bbox for table in tables for location in table.locations]
            page_bb.bboxes = [bbox for bbox in page_bb.bboxes if
                              not self._inside_any_unreadable_block(bbox.bbox, unreadable_blocks)]
            lines.append(self.metadata_extractor.extract_metadata_and_set_annotations(page_with_lines=page_bb,
                                                                                      call_classifier=False))

        # формирую lines с get_line_with_meta.
        # возвращаю
        return lines, tables, page.attachments, []

    def __handle_page(self, page: PDFPage, page_number: int, path: str,
                      parameters: ParametersForParseDoc, layout) -> PageWithBBox:

        # device, interpreter = self.__get_interpreter()
        # try:
        #     interpreter.process_page(page)
        # except Exception as e:
        #     raise BadFileFormatError(f"can't handle file {path} get {e}")

        image_page = self.__get_image(path=path, page_num=page_number)
        image_height, image_width, *_ = image_page.shape

        height = int(page.mediabox[3])
        width = int(page.mediabox[2])
        if height > 0 and width > 0:
            k_w, k_h = image_width / page.mediabox[2], image_height / page.mediabox[3]
            page_broken = False
        else:
            page_broken = True
            k_w, k_h = None, None

        if self.config.get("debug_mode", False):
            self.__debug_extract_layout(image_page, layout, page_number, k_w, k_h, page, width, height)

        # 1. extract textline objects and image (as LTImage)
        images = []
        layout_objects = [lobj for lobj in layout]
        lobjs_textline = []
        for lobj in layout_objects:
            if isinstance(lobj, LTTextBoxHorizontal):
                lines = [lobj_text for lobj_text in lobj if isinstance(lobj_text, LTTextLineHorizontal)]
                lines.sort(key=lambda lobj: float(height - lobj.y1))
                lobjs_textline.extend(lines)
            elif isinstance(lobj, LTTextLineHorizontal):
                lobjs_textline.append(lobj)

            elif isinstance(lobj, LTFigure) and not page_broken and parameters.with_attachments:
                attachment = self.__extract_image(parameters, height, image_page, k_h, k_w, lobj, page_number)
                if attachment is not None:
                    images.append(attachment)

        bboxes = []
        for line_num, lobj in enumerate(lobjs_textline):
            text_with_bbox = self.get_info_layout_object(lobj, page_num=page_number, line_num=line_num, k_w=k_w,
                                                         k_h=k_h, height=height, width=width)
            if text_with_bbox.bbox.width * text_with_bbox.bbox.height > 0:
                bboxes.append(text_with_bbox)

        attachments = images if len(images) < 10 else []

        return PageWithBBox(bboxes=bboxes, image=image_page, page_num=page_number, attachments=attachments,
                            pdf_page_height=height, pdf_page_width=width)

    def __get_interpreter(self) -> Tuple[PDFPageAggregator, PDFPageInterpreter]:
        rsrcmgr = PDFResourceManager()
        laparams = LAParams(line_margin=1.5, line_overlap=0.5, boxes_flow=0.5, word_margin=0.1, char_margin=3,
                            detect_vertical=False)
        device = PDFPageAggregator(rsrcmgr, laparams=laparams)
        interpreter = PDFPageInterpreter(rsrcmgr, device)
        return device, interpreter

    @staticmethod
    def __get_image(path: str, page_num: int) -> np.ndarray:
        image_page = np.array(get_page_image(path=path, page_id=page_num))
        image_page = np.array(image_page)
        if len(image_page.shape) == 2:
            image_page = cv2.cvtColor(image_page, cv2.COLOR_GRAY2BGR)
        return image_page

    def __debug_extract_layout(self, image_src: np.ndarray, layout: LTContainer, page_num: int, k_w: float, k_h: float,
                               page: PDFPage,
                               width: int, height: int) -> None:
        """
        Function for debugging of pdfminer.six layout
        :param layout: container of layout element
        :return: None
        """
        tmp_dir = os.path.join(get_path_param(self.config, "path_debug"), "pdfminer")
        os.makedirs(tmp_dir, exist_ok=True)

        file_text = open(os.path.join(tmp_dir, f"text_{page_num}.txt"), "wt")

        # 1. extract layout objects
        lobjs = [lobj for lobj in layout]
        lobjs_textline = []
        lobjs_box = []
        lobjs_words = []
        lobjs_figures = []
        lobjs_images = []
        lobjs_curves = []
        annotations = []

        for lobj in lobjs:
            if isinstance(lobj, LTTextBoxHorizontal):
                lobj_annotations, _ = self.__extract_words_bbox_annotation(lobj, height, width)
                annotations.extend(lobj_annotations)
                lobjs_textline.extend(lobj)
            elif isinstance(lobj, LTTextLineHorizontal):
                lobj_annotations, _ = self.__extract_words_bbox_annotation(lobj, height, width)
                annotations.extend(lobj_annotations)
                lobjs_textline.append(lobj)
            elif isinstance(lobj, LTRect):
                lobjs_box.append(lobj)
            elif isinstance(lobj, LTFigure):
                lobjs_figures.append(lobj)
            elif isinstance(lobj, LTImage):
                lobjs_images.append(lobj)
            elif isinstance(lobj, LTCurve):
                lobjs_curves.append(lobj)
            elif isinstance(lobj, LTTextBox):
                lobjs_words.append(lobj)
        # 3. print information
        draw_annotation(image_src, annotations)
        """
        Call for debugging other LT elements:
        self.__draw_layout_element(image_src, lobjs_textline, file_text, k_w, k_h, page, (0, 255, 0))
        self.__draw_layout_element(image_src, lobjs_words, file_text, k_w, k_h, page, (0, 255, 0))
        self.__draw_layout_element(image_src, lobjs_box, file_text, k_w, k_h, page, (0, 0, 255), text="LTRect")
        self.__draw_layout_element(image_src, lobjs_figures, file_text, k_w, k_h, page, (255, 0, 0), text="LTFigure")
        self.__draw_layout_element(image_src, lobjs_images, file_text, k_w, k_h, page, (0, 255, 255), text="LTImage")
        self.__draw_layout_element(image_src, lobjs_curves, file_text, k_w, k_h, page, (0, 255, 255), text="LTCurve")'''
        """
        cv2.imwrite(os.path.join(tmp_dir, f"img_page_{page_num}.png"), image_src)
        file_text.close()

    def __extract_image(self,
                        parameters: ParametersForParseDoc,
                        height: int,
                        image_page: np.ndarray,
                        k_h: float,
                        k_w: float,
                        lobj: LTContainer,
                        page_number: int) -> Optional[PdfImageAttachment]:
        try:
            bbox = create_bbox(k_h=k_h, k_w=k_w, height=height, lobj=lobj)
            location = Location(bbox=bbox, page_number=page_number)
            cropped = image_page[bbox.y_top_left: bbox.y_bottom_right, bbox.x_top_left: bbox.x_bottom_right]
            uid = f"fig_{uuid.uuid1()}"
            file_name = f"{uid}.png"
            path_out = os.path.join(parameters.attachments_dir, file_name)
            Image.fromarray(cropped).save(path_out)
            attachment = PdfImageAttachment(
                original_name=file_name,
                tmp_file_path=path_out,
                need_content_analysis=parameters.need_content_analysis,
                uid=uid,
                location=location
            )
        except Exception as ex:
            self.logger.error(ex)
            attachment = None

        return attachment

    def get_info_layout_object(self, lobj: LTContainer, page_num: int, line_num: int, k_w: float, k_h: float,
                               height: int, width: int) -> TextWithBBox:
        # 1 - converting coordinate from pdf format into image
        bbox = create_bbox(height, k_h, k_w, lobj)

        # 2 - extract text and text annotations from current object
        annotations = []
        words = []
        if isinstance(lobj, LTTextLineHorizontal):
            # get line's annotations
            annotations, words = self.__get_line_annotations(lobj, height, width)

        text_with_bbox = TextWithBBox(bbox=bbox, page_num=page_num, words=words, line_num=line_num,
                                      annotations=annotations)

        if self.config.get("labeling_mode", False):  # add bbox for a textual line
            bbox = create_bbox(height=height, k_h=1.0, k_w=1.0, lobj=lobj)
            text_with_bbox.annotations.append(
                BBoxAnnotation(start=0, end=len(text_with_bbox.text), value=bbox, page_width=width, page_height=height))

        return text_with_bbox

    def __get_line_annotations(self, lobj: LTTextLineHorizontal, height: int, width: int) -> Tuple[
        List[Annotation], List[WordWithBBox]]:
        # 1 - prepare data for group by name
        chars_with_style = []
        rand_weight = self._get_new_weight()
        prev_style = ""

        for lobj_char in lobj:
            if isinstance(lobj_char, LTChar) or isinstance(lobj_char, LTAnno):
                # get styles
                if len(chars_with_style) > 0:
                    # check next char different from previously then we fresh rand_weight
                    prev_style, prev_size = chars_with_style[-1].split("_rand_")

                if isinstance(lobj_char, LTChar) and lobj_char.get_text() not in (" ", "\n", "\t"):
                    curr_style = f"{lobj_char.fontname}_{round(lobj_char.size, 0)}"

                    if curr_style != prev_style:
                        rand_weight = self._get_new_weight()

                    chars_with_style.append(f"{curr_style}_rand_{rand_weight}")
                elif lobj_char.get_text() in (" ", "\n", "\t") and len(chars_with_style) > 0:
                    # check on the space or \n
                    # duplicated previous style
                    chars_with_style.append(chars_with_style[-1])

        annotations, words = self.__extract_words_bbox_annotation(lobj, height, width)
        if self.config.get("labeling_mode", False):
            annotations = []  # ignore bboxes of words

        # 3 - extract range from chars_with_style array
        char_pointer = 0
        for key, group in itertools.groupby(chars_with_style, lambda x: x):
            count_chars = len(list(group))
            annotations.extend(self.__parse_style_string(key, char_pointer, char_pointer + count_chars - 1))
            char_pointer += count_chars

        return annotations, words

    def _get_new_weight(self) -> str:
        return binascii.hexlify(os.urandom(8)).decode("ascii")

    def __parse_style_string(self, chars_with_meta: str, begin: int, end: int) -> List[Annotation]:
        # style parsing
        annotations = []
        prev_style, _ = chars_with_meta.split("_rand_")
        font, size, *_ = prev_style.split("_")
        fontname_wo_rand = font.split("+")[-1]
        styles = fontname_wo_rand.split("-")[-1]
        annotations.append(StyleAnnotation(begin, end, value=fontname_wo_rand))

        if "Bold" in styles:
            annotations.append(BoldAnnotation(begin, end, value="True"))
        if "Italic" in styles:
            annotations.append(ItalicAnnotation(begin, end, value="True"))

        if size.replace(".", "", 1).isnumeric():
            annotations.append(SizeAnnotation(begin, end, value=size))

        return annotations

    def __extract_words_bbox_annotation(self, lobj: LTTextContainer, height: int, width: int) -> Tuple[
        List[Annotation], List[WordWithBBox]]:
        words: List[WordObj] = []
        word: WordObj = WordObj(start=0, end=0, value=LTTextContainer())
        if isinstance(lobj, LTTextLineHorizontal):
            lobj = [lobj]

        for text_line in lobj:
            for item, lobj_char in enumerate(text_line):
                if isinstance(lobj_char, LTChar) and lobj_char.get_text() not in (" ", "\n", "\t"):
                    word = word._replace(end=word.end + 1)
                    word.value.add(lobj_char)
                elif lobj_char.get_text() in (" ", "\n", "\t"):
                    if word.value._objs:
                        words.append(word)
                    word = WordObj(start=item + 1, end=item + 1, value=LTTextContainer())

        annotations, words_with_bbox = [], []
        for word in words:
            word_bbox = create_bbox(height=height, k_h=1.0, k_w=1.0, lobj=word.value)
            annotations.append(
                BBoxAnnotation(start=word.start, end=word.end, value=word_bbox, page_width=width, page_height=height))
            words_with_bbox.append(WordWithBBox(text=word.value.get_text(), bbox=word_bbox))

        return annotations, words_with_bbox

    def _inside_any_unreadable_block(self, obj_bbox: BBox, unreadable_blocks: List[BBox]) -> bool:
        """
        Check obj_bbox inside some unreadable blocks or not
        :param obj_bbox: ["x_top_left", "y_top_left", "width", "height"]
        :param unreadable_blocks: List["x_top_left", "y_top_left", "width", "height"]
        :return: Boolean
        """
        for block in unreadable_blocks:
            if block.have_intersection_with_box(obj_bbox):
                return True
        return False
