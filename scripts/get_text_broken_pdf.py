from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdf_broken_encoding_reader.pdf_broken_encoding_reader import \
    PdfBrokenEncodingReader
reader = PdfBrokenEncodingReader()
pdf_path = "../tests/data/pdf_with_text_layer/mongolo.pdf"
d = reader.read(pdf_path)
print("finished")
print(d.get_text())
