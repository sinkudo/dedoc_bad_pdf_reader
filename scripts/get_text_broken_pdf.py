import argparse

from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdf_broken_encoding_reader.pdf_broken_encoding_reader import PdfBrokenEncodingReader

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Выбор модели для извлечения текста и путь до PDF файла")
    parser.add_argument("--pdf_path", help="Путь до PDF файла", default="../tests/data/pdf_with_text_layer/mongolo.pdf")
    parser.add_argument("--model_name", help="Модель: rus, eng, ruseng", default='ruseng')
    args = parser.parse_args()
    pdf_path = args.pdf_path
    reader = PdfBrokenEncodingReader(model_lang='ruseng')
    doc = reader.read(pdf_path)
    print('finished')
    print(doc.get_text())
