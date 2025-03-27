import argparse

from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdf_broken_encoding_reader.pdf_broken_encoding_reader import PdfBrokenEncodingReader

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf_path", help="PDF file path", default="../tests/data/pdf_with_text_layer/mongolo.pdf")
    args = parser.parse_args()
    reader = PdfBrokenEncodingReader()
    document = reader.read(args.pdf_path)
    print(document.get_text())
