#!/usr/bin/python3
import os
import re
from io import BytesIO
from struct import pack, unpack
from sys import argv
from math import ceil

imap_pos = 0xc
int_mmap_pos = 0x18
mmap_pos = 0x2c


def read_ident(f):
	end = None
	sig = f.read(4)
	if sig == b'XFIR':
		end = '<'
	elif sig == b'RIFX':
		end = '>'
	return end


def read_tag(f, endian='<'):
	s = f.read(4)
	if endian == '<':
		s = s[::-1]
	return(s.decode('ascii'))


def read_i16(f, endian='<'):
	data, = unpack(endian+'H', f.read(2))
	return(data)


def read_i32(f, endian='<'):
	data, = unpack(endian+'I', f.read(4))
	return(data)


def write_i32(f, data, endian='<'):
	data = pack(endian+'I', data)
	f.write(data)
	return


def parse_dict(data, endian='<'):
	d = BytesIO(data[8:])
	toclen, = unpack(endian+'I', d.read(4))
	if toclen > 0x10000:
		# Win16 EXEs swap endianness after the tag size
		endian = {'>': '<', '<': '>'}[endian]
		d.seek(0)
		toclen, = unpack(endian+'I', d.read(4))
	if endian == '<':
		toclen += 4
	else:
		toclen += 2  # ?????????
	d.seek(0x10)
	len_names, = unpack(endian+'I', d.read(4))
	d.seek(0x18)
	d.read(toclen)
	names = []
	for i in range(len_names):
		lname, = unpack(endian+'I', d.read(4))
		filler = lname % 4
		if filler:
			filler = 4 - filler
		fname = d.read(lname)
		assert lname == len(fname)
		d.read(filler)
		try:
			names.append(fname.decode('utf-8'))
		except UnicodeDecodeError:
			names.append(fname.decode('shift-jis'))
	return names


file = argv[1]
f = open(file, 'rb').read()
win_file = re.search(rb'XFIR.{4}LPPA', f, re.S)
mac_file = re.search(rb'RIFX.{4}APPL', f, re.S)
if win_file:
	off = win_file.start()
elif mac_file:
	off = mac_file.start()
else:
	off_fix_check = re.search(rb'(?:XFIR|RIFX).{4}(?:MV93|39VM)', f)
	if off_fix_check:
		if off_fix_check.start() != 0:
			outfile, ext = os.path.splitext(argv[1])
			f = BytesIO(f[off_fix_check.start():])
			endian = read_ident(f)
			size, = unpack(endian+'I', f.read(4))
			f.seek(0)  # Seek to the beginning
			open('NEW.'.join([outfile, ext]), 'wb').write(f.read(size + 8))
	else:
		print('not a Director application')
		exit(1)

print(f'SW file found at 0x{off:x}')
# projector = f[:off]
f = BytesIO(f[off:])
endian = read_ident(f)
f.seek(imap_pos)
assert read_tag(f, endian) == 'imap'
f.seek(0x8, 1)
off = unpack(endian+'I', f.read(4))[0] - mmap_pos
if not off:
	print('nothing to do')
	exit(1)

f.seek(mmap_pos)
assert read_tag(f, endian) == 'mmap'
mmap_ress_len = read_i32(f, endian) - 0x20
f.seek(mmap_pos + 0xa)
mmap_res_len = read_i16(f, endian)
mmap_ress_pos = mmap_pos + 0x20
f.seek(mmap_ress_pos + 0x8)
REL = read_i32(f, endian)
files = []
names = None
for i in range(ceil(mmap_ress_len / mmap_res_len)):
	f.seek((i * mmap_res_len) + mmap_ress_pos)
	tag = read_tag(f, endian)
	size, off = unpack(endian+'II', f.read(0x8))
	size += 8
	if off:
		off -= REL

	if tag == 'File':
		files.append((off, size))
	elif tag == 'Dict':
		f.seek(off)
		names = parse_dict(f.read(size), endian)
	else:
		# Don't process anything after Files (junk data can screw this up)
		if len(files) > 1:
			break

files = list(zip(names, files))
outfolder, _ = os.path.splitext(argv[1])
if outfolder == argv[1]:
	outfolder += '_out'
try:
	os.mkdir(outfolder)
except FileExistsError:
	pass

for n in [f for f in files if not re.search(r'\.x(?:16|32)$', f[0], re.I)]:
	name, file = n
	if win_file:
		oname, = re.findall(r'([^\\]+)$', name)
	else:
		# Director uses `:` as the path separator on Mac, even Intel/OSX!
		oname, = re.findall(r'([^:]+)$', name)
	off, _ = file
	# f.seek(off)
	print(f'Original file path: {os.path.join(name)}')
	# The size indicated in the memory map is sometimes wrong (??),
	# so we need to get the real size from the header of the Director file
	f.seek(off+4)
	size, = unpack(endian+'I', f.read(0x4))
	size += 8
	f.seek(off)
	temp_file = BytesIO(f.read(size))
	temp_file_endian = read_ident(temp_file)
	temp_file.seek(0x8)
	file_type = read_tag(temp_file, temp_file_endian)
	extension = {'.dir': ['.dxr', '.dcr'], '.cst': ['.cxt', '.cct']}
	oname_ext = oname.lower()[-4:]
	if oname_ext in extension:
		if file_type == 'MV93':
			oname_ext = extension[oname_ext][0]
		elif file_type == 'FGDM':
			oname_ext = extension[oname_ext][1]
		if oname[-4:].isupper():
			oname_ext = oname_ext.upper()
		oname = oname[:-4] + oname_ext

	if file_type in ['FGDM', 'FGDC']:
		temp_file.seek(0)
		open(os.path.join(outfolder, oname), 'wb').write(temp_file.read())
		continue
	temp_file.seek(0x36)
	mmap_res_len = read_i16(temp_file, temp_file_endian)
	temp_file.seek(0x30)
	mmap_ress_len = read_i32(temp_file, temp_file_endian) - 0x20
	temp_file.seek(0x54)
	relative = read_i32(temp_file, temp_file_endian)
	temp_file.seek(int_mmap_pos)
	write_i32(temp_file, mmap_pos, temp_file_endian)
	for i in range(mmap_ress_len // mmap_res_len):
		pos = (i * mmap_res_len) + 0x54
		temp_file.seek(pos)
		absolute = read_i32(temp_file, endian)
		if absolute:
			absolute -= relative
			temp_file.seek(pos)
			write_i32(temp_file, absolute, temp_file_endian)
	temp_file.seek(0)
	open(os.path.join(outfolder, oname), 'wb').write(temp_file.read())

# open(os.path.join(outfolder, 'projector.exe'), 'wb').write(projector)
