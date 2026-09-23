#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B 站硬核会员 JEV 自动答题（单文件版，不装任何第三方库）

在 PowerShell 里跑：
    python .\\bili_hardcore_jev.py             # 完整流程：登录 -> 答题 -> 出分
    python .\\bili_hardcore_jev.py --selftest  # 自检（测 JEV key + 测二维码），不动 B 站账号
    python .\\bili_hardcore_jev.py --status    # 看登录状态和账号等级
    python .\\bili_hardcore_jev.py --result    # 查分
    python .\\bili_hardcore_jev.py --logout    # 退出登录（删掉本机保存的登录凭证）

几件事先说清楚：
1. 手机只用做两件事：第一次扫码登录；看验证码图片。答题全部走 B 站手机端
   的接口（脚本在电脑上“扮成”B 站安卓版），不用拿手机答题。
2. 账号必须满 6 级；每天只有 3 次答题机会，跑一次消耗一次。
3. JEV key 从文本文件读取（第一行就是 key）：默认找脚本同目录的 jev.txt，
   也可以用 --key-file 指定路径，或者设环境变量 JEV_KEY_FILE。
4. 登录凭证存在 ~/.bili-hardcore/auth.json，7 天后自动失效；
   这个文件属于敏感信息，不要发给别人。

内嵌的开源代码均为 MIT 协议，版权头保留在文件里：
- B 站接口层：Karben233/bili-hardcore 的 bili_quiz.py
- 二维码生成器：nayuki/QR-Code-generator（经 tylevnovik/bili-hardcore-skill 移植）
"""

# ===== 内置：开源二维码生成器（下面这块是原样照抄的第三方代码）=====
# 
# QR Code generator library (Python)
# 
# Copyright (c) Project Nayuki. (MIT License)
# https://www.nayuki.io/page/qr-code-generator-library
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
# - The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
# - The Software is provided "as is", without warranty of any kind, express or
#   implied, including but not limited to the warranties of merchantability,
#   fitness for a particular purpose and noninfringement. In no event shall the
#   authors or copyright holders be liable for any claim, damages or other
#   liability, whether in an action of contract, tort or otherwise, arising from,
#   out of or in connection with the Software or the use or other dealings in the
#   Software.
# 

from __future__ import annotations
import collections, itertools, re
from collections.abc import Sequence
from typing import Optional, Union


# ---- QR Code symbol class ----

class QrCode:
	"""A QR Code symbol, which is a type of two-dimension barcode.
	Invented by Denso Wave and described in the ISO/IEC 18004 standard.
	Instances of this class represent an immutable square grid of dark and light cells.
	The class provides static factory functions to create a QR Code from text or binary data.
	The class covers the QR Code Model 2 specification, supporting all versions (sizes)
	from 1 to 40, all 4 error correction levels, and 4 character encoding modes.
	
	Ways to create a QR Code object:
	- High level: Take the payload data and call QrCode.encode_text() or QrCode.encode_binary().
	- Mid level: Custom-make the list of segments and call QrCode.encode_segments().
	- Low level: Custom-make the array of data codeword bytes (including
	  segment headers and final padding, excluding error correction codewords),
	  supply the appropriate version number, and call the QrCode() constructor.
	(Note that all ways require supplying the desired error correction level.)"""
	
	# ---- Static factory functions (high level) ----
	
	@staticmethod
	def encode_text(text: str, ecl: QrCode.Ecc) -> QrCode:
		"""Returns a QR Code representing the given Unicode text string at the given error correction level.
		As a conservative upper bound, this function is guaranteed to succeed for strings that have 738 or fewer
		Unicode code points (not UTF-16 code units) if the low error correction level is used. The smallest possible
		QR Code version is automatically chosen for the output. The ECC level of the result may be higher than the
		ecl argument if it can be done without increasing the version."""
		segs: list[QrSegment] = QrSegment.make_segments(text)
		return QrCode.encode_segments(segs, ecl)
	
	
	@staticmethod
	def encode_binary(data: Union[bytes,Sequence[int]], ecl: QrCode.Ecc) -> QrCode:
		"""Returns a QR Code representing the given binary data at the given error correction level.
		This function always encodes using the binary segment mode, not any text mode. The maximum number of
		bytes allowed is 2953. The smallest possible QR Code version is automatically chosen for the output.
		The ECC level of the result may be higher than the ecl argument if it can be done without increasing the version."""
		return QrCode.encode_segments([QrSegment.make_bytes(data)], ecl)
	
	
	# ---- Static factory functions (mid level) ----
	
	@staticmethod
	def encode_segments(segs: Sequence[QrSegment], ecl: QrCode.Ecc, minversion: int = 1, maxversion: int = 40, mask: int = -1, boostecl: bool = True) -> QrCode:
		"""Returns a QR Code representing the given segments with the given encoding parameters.
		The smallest possible QR Code version within the given range is automatically
		chosen for the output. Iff boostecl is true, then the ECC level of the result
		may be higher than the ecl argument if it can be done without increasing the
		version. The mask number is either between 0 to 7 (inclusive) to force that
		mask, or -1 to automatically choose an appropriate mask (which may be slow).
		This function allows the user to create a custom sequence of segments that switches
		between modes (such as alphanumeric and byte) to encode text in less space.
		This is a mid-level API; the high-level API is encode_text() and encode_binary()."""
		
		if not (QrCode.MIN_VERSION <= minversion <= maxversion <= QrCode.MAX_VERSION) or not (-1 <= mask <= 7):
			raise ValueError("Invalid value")
		
		# Find the minimal version number to use
		for version in range(minversion, maxversion + 1):
			datacapacitybits: int = QrCode._get_num_data_codewords(version, ecl) * 8  # Number of data bits available
			datausedbits: Optional[int] = QrSegment.get_total_bits(segs, version)
			if (datausedbits is not None) and (datausedbits <= datacapacitybits):
				break  # This version number is found to be suitable
			if version >= maxversion:  # All versions in the range could not fit the given data
				msg: str = "Segment too long"
				if datausedbits is not None:
					msg = f"Data length = {datausedbits} bits, Max capacity = {datacapacitybits} bits"
				raise DataTooLongError(msg)
		assert datausedbits is not None
		
		# Increase the error correction level while the data still fits in the current version number
		for newecl in (QrCode.Ecc.MEDIUM, QrCode.Ecc.QUARTILE, QrCode.Ecc.HIGH):  # From low to high
			if boostecl and (datausedbits <= QrCode._get_num_data_codewords(version, newecl) * 8):
				ecl = newecl
		
		# Concatenate all segments to create the data bit string
		bb = _BitBuffer()
		for seg in segs:
			bb.append_bits(seg.get_mode().get_mode_bits(), 4)
			bb.append_bits(seg.get_num_chars(), seg.get_mode().num_char_count_bits(version))
			bb.extend(seg._bitdata)
		assert len(bb) == datausedbits
		
		# Add terminator and pad up to a byte if applicable
		datacapacitybits = QrCode._get_num_data_codewords(version, ecl) * 8
		assert len(bb) <= datacapacitybits
		bb.append_bits(0, min(4, datacapacitybits - len(bb)))
		bb.append_bits(0, -len(bb) % 8)  # Note: Python's modulo on negative numbers behaves better than C family languages
		assert len(bb) % 8 == 0
		
		# Pad with alternating bytes until data capacity is reached
		for padbyte in itertools.cycle((0xEC, 0x11)):
			if len(bb) >= datacapacitybits:
				break
			bb.append_bits(padbyte, 8)
		
		# Pack bits into bytes in big endian
		datacodewords = bytearray([0] * (len(bb) // 8))
		for (i, bit) in enumerate(bb):
			datacodewords[i >> 3] |= bit << (7 - (i & 7))
		
		# Create the QR Code object
		return QrCode(version, ecl, datacodewords, mask)
	
	
	# ---- Private fields ----
	
	# The version number of this QR Code, which is between 1 and 40 (inclusive).
	# This determines the size of this barcode.
	_version: int
	
	# The width and height of this QR Code, measured in modules, between
	# 21 and 177 (inclusive). This is equal to version * 4 + 17.
	_size: int
	
	# The error correction level used in this QR Code.
	_errcorlvl: QrCode.Ecc
	
	# The index of the mask pattern used in this QR Code, which is between 0 and 7 (inclusive).
	# Even if a QR Code is created with automatic masking requested (mask = -1),
	# the resulting object still has a mask value between 0 and 7.
	_mask: int
	
	# The modules of this QR Code (False = light, True = dark).
	# Immutable after constructor finishes. Accessed through get_module().
	_modules: list[list[bool]]
	
	# Indicates function modules that are not subjected to masking. Discarded when constructor finishes.
	_isfunction: list[list[bool]]
	
	
	# ---- Constructor (low level) ----
	
	def __init__(self, version: int, errcorlvl: QrCode.Ecc, datacodewords: Union[bytes,Sequence[int]], msk: int) -> None:
		"""Creates a new QR Code with the given version number,
		error correction level, data codeword bytes, and mask number.
		This is a low-level API that most users should not use directly.
		A mid-level API is the encode_segments() function."""
		
		# Check scalar arguments and set fields
		if not (QrCode.MIN_VERSION <= version <= QrCode.MAX_VERSION):
			raise ValueError("Version value out of range")
		if not (-1 <= msk <= 7):
			raise ValueError("Mask value out of range")
		
		self._version = version
		self._size = version * 4 + 17
		self._errcorlvl = errcorlvl
		
		# Initialize both grids to be size*size arrays of Boolean false
		self._modules    = [[False] * self._size for _ in range(self._size)]  # Initially all light
		self._isfunction = [[False] * self._size for _ in range(self._size)]
		
		# Compute ECC, draw modules
		self._draw_function_patterns()
		allcodewords: bytes = self._add_ecc_and_interleave(bytearray(datacodewords))
		self._draw_codewords(allcodewords)
		
		# Do masking
		if msk == -1:  # Automatically choose best mask
			minpenalty: int = 1 << 32
			for i in range(8):
				self._apply_mask(i)
				self._draw_format_bits(i)
				penalty = self._get_penalty_score()
				if penalty < minpenalty:
					msk = i
					minpenalty = penalty
				self._apply_mask(i)  # Undoes the mask due to XOR
		assert 0 <= msk <= 7
		self._mask = msk
		self._apply_mask(msk)  # Apply the final choice of mask
		self._draw_format_bits(msk)  # Overwrite old format bits
		
		del self._isfunction
	
	
	# ---- Accessor methods ----
	
	def get_version(self) -> int:
		"""Returns this QR Code's version number, in the range [1, 40]."""
		return self._version
	
	def get_size(self) -> int:
		"""Returns this QR Code's size, in the range [21, 177]."""
		return self._size
	
	def get_error_correction_level(self) -> QrCode.Ecc:
		"""Returns this QR Code's error correction level."""
		return self._errcorlvl
	
	def get_mask(self) -> int:
		"""Returns this QR Code's mask, in the range [0, 7]."""
		return self._mask
	
	def get_module(self, x: int, y: int) -> bool:
		"""Returns the color of the module (pixel) at the given coordinates, which is False
		for light or True for dark. The top left corner has the coordinates (x=0, y=0).
		If the given coordinates are out of bounds, then False (light) is returned."""
		return (0 <= x < self._size) and (0 <= y < self._size) and self._modules[y][x]
	
	
	# ---- Private helper methods for constructor: Drawing function modules ----
	
	def _draw_function_patterns(self) -> None:
		"""Reads this object's version field, and draws and marks all function modules."""
		# Draw horizontal and vertical timing patterns
		for i in range(self._size):
			self._set_function_module(6, i, i % 2 == 0)
			self._set_function_module(i, 6, i % 2 == 0)
		
		# Draw 3 finder patterns (all corners except bottom right; overwrites some timing modules)
		self._draw_finder_pattern(3, 3)
		self._draw_finder_pattern(self._size - 4, 3)
		self._draw_finder_pattern(3, self._size - 4)
		
		# Draw numerous alignment patterns
		alignpatpos: list[int] = self._get_alignment_pattern_positions()
		numalign: int = len(alignpatpos)
		skips: Sequence[tuple[int,int]] = ((0, 0), (0, numalign - 1), (numalign - 1, 0))
		for i in range(numalign):
			for j in range(numalign):
				if (i, j) not in skips:  # Don't draw on the three finder corners
					self._draw_alignment_pattern(alignpatpos[i], alignpatpos[j])
		
		# Draw configuration data
		self._draw_format_bits(0)  # Dummy mask value; overwritten later in the constructor
		self._draw_version()
	
	
	def _draw_format_bits(self, mask: int) -> None:
		"""Draws two copies of the format bits (with its own error correction code)
		based on the given mask and this object's error correction level field."""
		# Calculate error correction code and pack bits
		data: int = self._errcorlvl.formatbits << 3 | mask  # errCorrLvl is uint2, mask is uint3
		rem: int = data
		for _ in range(10):
			rem = (rem << 1) ^ ((rem >> 9) * 0x537)
		bits: int = (data << 10 | rem) ^ 0x5412  # uint15
		assert bits >> 15 == 0
		
		# Draw first copy
		for i in range(0, 6):
			self._set_function_module(8, i, _get_bit(bits, i))
		self._set_function_module(8, 7, _get_bit(bits, 6))
		self._set_function_module(8, 8, _get_bit(bits, 7))
		self._set_function_module(7, 8, _get_bit(bits, 8))
		for i in range(9, 15):
			self._set_function_module(14 - i, 8, _get_bit(bits, i))
		
		# Draw second copy
		for i in range(0, 8):
			self._set_function_module(self._size - 1 - i, 8, _get_bit(bits, i))
		for i in range(8, 15):
			self._set_function_module(8, self._size - 15 + i, _get_bit(bits, i))
		self._set_function_module(8, self._size - 8, True)  # Always dark
	
	
	def _draw_version(self) -> None:
		"""Draws two copies of the version bits (with its own error correction code),
		based on this object's version field, iff 7 <= version <= 40."""
		if self._version < 7:
			return
		
		# Calculate error correction code and pack bits
		rem: int = self._version  # version is uint6, in the range [7, 40]
		for _ in range(12):
			rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
		bits: int = self._version << 12 | rem  # uint18
		assert bits >> 18 == 0
		
		# Draw two copies
		for i in range(18):
			bit: bool = _get_bit(bits, i)
			a: int = self._size - 11 + i % 3
			b: int = i // 3
			self._set_function_module(a, b, bit)
			self._set_function_module(b, a, bit)
	
	
	def _draw_finder_pattern(self, x: int, y: int) -> None:
		"""Draws a 9*9 finder pattern including the border separator,
		with the center module at (x, y). Modules can be out of bounds."""
		for dy in range(-4, 5):
			for dx in range(-4, 5):
				xx, yy = x + dx, y + dy
				if (0 <= xx < self._size) and (0 <= yy < self._size):
					# Chebyshev/infinity norm
					self._set_function_module(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))
	
	
	def _draw_alignment_pattern(self, x: int, y: int) -> None:
		"""Draws a 5*5 alignment pattern, with the center module
		at (x, y). All modules must be in bounds."""
		for dy in range(-2, 3):
			for dx in range(-2, 3):
				self._set_function_module(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)
	
	
	def _set_function_module(self, x: int, y: int, isdark: bool) -> None:
		"""Sets the color of a module and marks it as a function module.
		Only used by the constructor. Coordinates must be in bounds."""
		assert type(isdark) is bool
		self._modules[y][x] = isdark
		self._isfunction[y][x] = True
	
	
	# ---- Private helper methods for constructor: Codewords and masking ----
	
	def _add_ecc_and_interleave(self, data: bytearray) -> bytes:
		"""Returns a new byte string representing the given data with the appropriate error correction
		codewords appended to it, based on this object's version and error correction level."""
		version: int = self._version
		assert len(data) == QrCode._get_num_data_codewords(version, self._errcorlvl)
		
		# Calculate parameter numbers
		numblocks: int = QrCode._NUM_ERROR_CORRECTION_BLOCKS[self._errcorlvl.ordinal][version]
		blockecclen: int = QrCode._ECC_CODEWORDS_PER_BLOCK  [self._errcorlvl.ordinal][version]
		rawcodewords: int = QrCode._get_num_raw_data_modules(version) // 8
		numshortblocks: int = numblocks - rawcodewords % numblocks
		shortblocklen: int = rawcodewords // numblocks
		
		# Split data into blocks and append ECC to each block
		blocks: list[bytes] = []
		rsdiv: bytes = QrCode._reed_solomon_compute_divisor(blockecclen)
		k: int = 0
		for i in range(numblocks):
			dat: bytearray = data[k : k + shortblocklen - blockecclen + (0 if i < numshortblocks else 1)]
			k += len(dat)
			ecc: bytes = QrCode._reed_solomon_compute_remainder(dat, rsdiv)
			if i < numshortblocks:
				dat.append(0)
			blocks.append(dat + ecc)
		assert k == len(data)
		
		# Interleave (not concatenate) the bytes from every block into a single sequence
		result = bytearray()
		for i in range(len(blocks[0])):
			for (j, blk) in enumerate(blocks):
				# Skip the padding byte in short blocks
				if (i != shortblocklen - blockecclen) or (j >= numshortblocks):
					result.append(blk[i])
		assert len(result) == rawcodewords
		return result
	
	
	def _draw_codewords(self, data: bytes) -> None:
		"""Draws the given sequence of 8-bit codewords (data and error correction) onto the entire
		data area of this QR Code. Function modules need to be marked off before this is called."""
		assert len(data) == QrCode._get_num_raw_data_modules(self._version) // 8
		
		i: int = 0  # Bit index into the data
		# Do the funny zigzag scan
		for right in range(self._size - 1, 0, -2):  # Index of right column in each column pair
			if right <= 6:
				right -= 1
			for vert in range(self._size):  # Vertical counter
				for j in range(2):
					x: int = right - j  # Actual x coordinate
					upward: bool = (right + 1) & 2 == 0
					y: int = (self._size - 1 - vert) if upward else vert  # Actual y coordinate
					if (not self._isfunction[y][x]) and (i < len(data) * 8):
						self._modules[y][x] = _get_bit(data[i >> 3], 7 - (i & 7))
						i += 1
					# If this QR Code has any remainder bits (0 to 7), they were assigned as
					# 0/false/light by the constructor and are left unchanged by this method
		assert i == len(data) * 8
	
	
	def _apply_mask(self, mask: int) -> None:
		"""XORs the codeword modules in this QR Code with the given mask pattern.
		The function modules must be marked and the codeword bits must be drawn
		before masking. Due to the arithmetic of XOR, calling _apply_mask() with
		the same mask value a second time will undo the mask. A final well-formed
		QR Code needs exactly one (not zero, two, etc.) mask applied."""
		if not (0 <= mask <= 7):
			raise ValueError("Mask value out of range")
		masker: collections.abc.Callable[[int,int],int] = QrCode._MASK_PATTERNS[mask]
		for y in range(self._size):
			for x in range(self._size):
				self._modules[y][x] ^= (masker(x, y) == 0) and (not self._isfunction[y][x])
	
	
	def _get_penalty_score(self) -> int:
		"""Calculates and returns the penalty score based on state of this QR Code's current modules.
		This is used by the automatic mask choice algorithm to find the mask pattern that yields the lowest score."""
		result: int = 0
		size: int = self._size
		modules: list[list[bool]] = self._modules
		
		# Adjacent modules in row having same color, and finder-like patterns
		for y in range(size):
			runcolor: bool = False
			runx: int = 0
			runhistory = collections.deque([0] * 7, 7)
			for x in range(size):
				if modules[y][x] == runcolor:
					runx += 1
					if runx == 5:
						result += QrCode._PENALTY_N1
					elif runx > 5:
						result += 1
				else:
					self._finder_penalty_add_history(runx, runhistory)
					if not runcolor:
						result += self._finder_penalty_count_patterns(runhistory) * QrCode._PENALTY_N3
					runcolor = modules[y][x]
					runx = 1
			result += self._finder_penalty_terminate_and_count(runcolor, runx, runhistory) * QrCode._PENALTY_N3
		# Adjacent modules in column having same color, and finder-like patterns
		for x in range(size):
			runcolor = False
			runy: int = 0
			runhistory = collections.deque([0] * 7, 7)
			for y in range(size):
				if modules[y][x] == runcolor:
					runy += 1
					if runy == 5:
						result += QrCode._PENALTY_N1
					elif runy > 5:
						result += 1
				else:
					self._finder_penalty_add_history(runy, runhistory)
					if not runcolor:
						result += self._finder_penalty_count_patterns(runhistory) * QrCode._PENALTY_N3
					runcolor = modules[y][x]
					runy = 1
			result += self._finder_penalty_terminate_and_count(runcolor, runy, runhistory) * QrCode._PENALTY_N3
		
		# 2*2 blocks of modules having same color
		for y in range(size - 1):
			for x in range(size - 1):
				if modules[y][x] == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
					result += QrCode._PENALTY_N2
		
		# Balance of dark and light modules
		dark: int = sum((1 if cell else 0) for row in modules for cell in row)
		total: int = size**2  # Note that size is odd, so dark/total != 1/2
		# Compute the smallest integer k >= 0 such that (45-5k)% <= dark/total <= (55+5k)%
		k: int = (abs(dark * 20 - total * 10) + total - 1) // total - 1
		assert 0 <= k <= 9
		result += k * QrCode._PENALTY_N4
		assert 0 <= result <= 2568888  # Non-tight upper bound based on default values of PENALTY_N1, ..., N4
		return result
	
	
	# ---- Private helper functions ----
	
	def _get_alignment_pattern_positions(self) -> list[int]:
		"""Returns an ascending list of positions of alignment patterns for this version number.
		Each position is in the range [0,177), and are used on both the x and y axes.
		This could be implemented as lookup table of 40 variable-length lists of integers."""
		if self._version == 1:
			return []
		else:
			numalign: int = self._version // 7 + 2
			step: int = (self._version * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
			result: list[int] = [(self._size - 7 - i * step) for i in range(numalign - 1)] + [6]
			return list(reversed(result))
	
	
	@staticmethod
	def _get_num_raw_data_modules(ver: int) -> int:
		"""Returns the number of data bits that can be stored in a QR Code of the given version number, after
		all function modules are excluded. This includes remainder bits, so it might not be a multiple of 8.
		The result is in the range [208, 29648]. This could be implemented as a 40-entry lookup table."""
		if not (QrCode.MIN_VERSION <= ver <= QrCode.MAX_VERSION):
			raise ValueError("Version number out of range")
		result: int = (16 * ver + 128) * ver + 64
		if ver >= 2:
			numalign: int = ver // 7 + 2
			result -= (25 * numalign - 10) * numalign - 55
			if ver >= 7:
				result -= 36
		assert 208 <= result <= 29648
		return result
	
	
	@staticmethod
	def _get_num_data_codewords(ver: int, ecl: QrCode.Ecc) -> int:
		"""Returns the number of 8-bit data (i.e. not error correction) codewords contained in any
		QR Code of the given version number and error correction level, with remainder bits discarded.
		This stateless pure function could be implemented as a (40*4)-cell lookup table."""
		return QrCode._get_num_raw_data_modules(ver) // 8 \
			- QrCode._ECC_CODEWORDS_PER_BLOCK    [ecl.ordinal][ver] \
			* QrCode._NUM_ERROR_CORRECTION_BLOCKS[ecl.ordinal][ver]
	
	
	@staticmethod
	def _reed_solomon_compute_divisor(degree: int) -> bytes:
		"""Returns a Reed-Solomon ECC generator polynomial for the given degree. This could be
		implemented as a lookup table over all possible parameter values, instead of as an algorithm."""
		if not (1 <= degree <= 255):
			raise ValueError("Degree out of range")
		# Polynomial coefficients are stored from highest to lowest power, excluding the leading term which is always 1.
		# For example the polynomial x^3 + 255x^2 + 8x + 93 is stored as the uint8 array [255, 8, 93].
		result = bytearray([0] * (degree - 1) + [1])  # Start off with the monomial x^0
		
		# Compute the product polynomial (x - r^0) * (x - r^1) * (x - r^2) * ... * (x - r^{degree-1}),
		# and drop the highest monomial term which is always 1x^degree.
		# Note that r = 0x02, which is a generator element of this field GF(2^8/0x11D).
		root: int = 1
		for _ in range(degree):  # Unused variable i
			# Multiply the current product by (x - r^i)
			for j in range(degree):
				result[j] = QrCode._reed_solomon_multiply(result[j], root)
				if j + 1 < degree:
					result[j] ^= result[j + 1]
			root = QrCode._reed_solomon_multiply(root, 0x02)
		return result
	
	
	@staticmethod
	def _reed_solomon_compute_remainder(data: bytes, divisor: bytes) -> bytes:
		"""Returns the Reed-Solomon error correction codeword for the given data and divisor polynomials."""
		result = bytearray([0] * len(divisor))
		for b in data:  # Polynomial division
			factor: int = b ^ result.pop(0)
			result.append(0)
			for (i, coef) in enumerate(divisor):
				result[i] ^= QrCode._reed_solomon_multiply(coef, factor)
		return result
	
	
	@staticmethod
	def _reed_solomon_multiply(x: int, y: int) -> int:
		"""Returns the product of the two given field elements modulo GF(2^8/0x11D). The arguments and result
		are unsigned 8-bit integers. This could be implemented as a lookup table of 256*256 entries of uint8."""
		if (x >> 8 != 0) or (y >> 8 != 0):
			raise ValueError("Byte out of range")
		# Russian peasant multiplication
		z: int = 0
		for i in reversed(range(8)):
			z = (z << 1) ^ ((z >> 7) * 0x11D)
			z ^= ((y >> i) & 1) * x
		assert z >> 8 == 0
		return z
	
	
	def _finder_penalty_count_patterns(self, runhistory: collections.deque[int]) -> int:
		"""Can only be called immediately after a light run is added, and
		returns either 0, 1, or 2. A helper function for _get_penalty_score()."""
		n: int = runhistory[1]
		assert n <= self._size * 3
		core: bool = n > 0 and (runhistory[2] == runhistory[4] == runhistory[5] == n) and runhistory[3] == n * 3
		return (1 if (core and runhistory[0] >= n * 4 and runhistory[6] >= n) else 0) \
		     + (1 if (core and runhistory[6] >= n * 4 and runhistory[0] >= n) else 0)
	
	
	def _finder_penalty_terminate_and_count(self, currentruncolor: bool, currentrunlength: int, runhistory: collections.deque[int]) -> int:
		"""Must be called at the end of a line (row or column) of modules. A helper function for _get_penalty_score()."""
		if currentruncolor:  # Terminate dark run
			self._finder_penalty_add_history(currentrunlength, runhistory)
			currentrunlength = 0
		currentrunlength += self._size  # Add light border to final run
		self._finder_penalty_add_history(currentrunlength, runhistory)
		return self._finder_penalty_count_patterns(runhistory)
	
	
	def _finder_penalty_add_history(self, currentrunlength: int, runhistory: collections.deque[int]) -> None:
		if runhistory[0] == 0:
			currentrunlength += self._size  # Add light border to initial run
		runhistory.appendleft(currentrunlength)
	
	
	# ---- Constants and tables ----
	
	MIN_VERSION: int =  1  # The minimum version number supported in the QR Code Model 2 standard
	MAX_VERSION: int = 40  # The maximum version number supported in the QR Code Model 2 standard
	
	# For use in _get_penalty_score(), when evaluating which mask is best.
	_PENALTY_N1: int =  3
	_PENALTY_N2: int =  3
	_PENALTY_N3: int = 40
	_PENALTY_N4: int = 10
	
	_ECC_CODEWORDS_PER_BLOCK: Sequence[Sequence[int]] = (
		# Version: (note that index 0 is for padding, and is set to an illegal value)
		# 0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40    Error correction level
		(-1,  7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Low
		(-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),  # Medium
		(-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Quartile
		(-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30))  # High
	
	_NUM_ERROR_CORRECTION_BLOCKS: Sequence[Sequence[int]] = (
		# Version: (note that index 0 is for padding, and is set to an illegal value)
		# 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40    Error correction level
		(-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4,  4,  4,  4,  4,  6,  6,  6,  6,  7,  8,  8,  9,  9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),  # Low
		(-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5,  5,  8,  9,  9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),  # Medium
		(-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8,  8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),  # Quartile
		(-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81))  # High
	
	_MASK_PATTERNS: Sequence[collections.abc.Callable[[int,int],int]] = (
		(lambda x, y:  (x + y) % 2                  ),
		(lambda x, y:  y % 2                        ),
		(lambda x, y:  x % 3                        ),
		(lambda x, y:  (x + y) % 3                  ),
		(lambda x, y:  (x // 3 + y // 2) % 2        ),
		(lambda x, y:  x * y % 2 + x * y % 3        ),
		(lambda x, y:  (x * y % 2 + x * y % 3) % 2  ),
		(lambda x, y:  ((x + y) % 2 + x * y % 3) % 2),
	)
	
	
	# ---- Public helper enumeration ----
	
	class Ecc:
		ordinal: int  # (Public) In the range 0 to 3 (unsigned 2-bit integer)
		formatbits: int  # (Package-private) In the range 0 to 3 (unsigned 2-bit integer)
		
		"""The error correction level in a QR Code symbol. Immutable."""
		# Private constructor
		def __init__(self, i: int, fb: int) -> None:
			self.ordinal = i
			self.formatbits = fb
		
		# Placeholders
		LOW     : QrCode.Ecc
		MEDIUM  : QrCode.Ecc
		QUARTILE: QrCode.Ecc
		HIGH    : QrCode.Ecc
	
	# Public constants. Create them outside the class.
	Ecc.LOW      = Ecc(0, 1)  # The QR Code can tolerate about  7% erroneous codewords
	Ecc.MEDIUM   = Ecc(1, 0)  # The QR Code can tolerate about 15% erroneous codewords
	Ecc.QUARTILE = Ecc(2, 3)  # The QR Code can tolerate about 25% erroneous codewords
	Ecc.HIGH     = Ecc(3, 2)  # The QR Code can tolerate about 30% erroneous codewords



# ---- Data segment class ----

class QrSegment:
	"""A segment of character/binary/control data in a QR Code symbol.
	Instances of this class are immutable.
	The mid-level way to create a segment is to take the payload data
	and call a static factory function such as QrSegment.make_numeric().
	The low-level way to create a segment is to custom-make the bit buffer
	and call the QrSegment() constructor with appropriate values.
	This segment class imposes no length restrictions, but QR Codes have restrictions.
	Even in the most favorable conditions, a QR Code can only hold 7089 characters of data.
	Any segment longer than this is meaningless for the purpose of generating QR Codes."""
	
	# ---- Static factory functions (mid level) ----
	
	@staticmethod
	def make_bytes(data: Union[bytes,Sequence[int]]) -> QrSegment:
		"""Returns a segment representing the given binary data encoded in byte mode.
		All input byte lists are acceptable. Any text string can be converted to
		UTF-8 bytes (s.encode("UTF-8")) and encoded as a byte mode segment."""
		bb = _BitBuffer()
		for b in data:
			bb.append_bits(b, 8)
		return QrSegment(QrSegment.Mode.BYTE, len(data), bb)
	
	
	@staticmethod
	def make_numeric(digits: str) -> QrSegment:
		"""Returns a segment representing the given string of decimal digits encoded in numeric mode."""
		if not QrSegment.is_numeric(digits):
			raise ValueError("String contains non-numeric characters")
		bb = _BitBuffer()
		i: int = 0
		while i < len(digits):  # Consume up to 3 digits per iteration
			n: int = min(len(digits) - i, 3)
			bb.append_bits(int(digits[i : i + n]), n * 3 + 1)
			i += n
		return QrSegment(QrSegment.Mode.NUMERIC, len(digits), bb)
	
	
	@staticmethod
	def make_alphanumeric(text: str) -> QrSegment:
		"""Returns a segment representing the given text string encoded in alphanumeric mode.
		The characters allowed are: 0 to 9, A to Z (uppercase only), space,
		dollar, percent, asterisk, plus, hyphen, period, slash, colon."""
		if not QrSegment.is_alphanumeric(text):
			raise ValueError("String contains unencodable characters in alphanumeric mode")
		bb = _BitBuffer()
		for i in range(0, len(text) - 1, 2):  # Process groups of 2
			temp: int = QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[i]] * 45
			temp += QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[i + 1]]
			bb.append_bits(temp, 11)
		if len(text) % 2 > 0:  # 1 character remaining
			bb.append_bits(QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[-1]], 6)
		return QrSegment(QrSegment.Mode.ALPHANUMERIC, len(text), bb)
	
	
	@staticmethod
	def make_segments(text: str) -> list[QrSegment]:
		"""Returns a new mutable list of zero or more segments to represent the given Unicode text string.
		The result may use various segment modes and switch modes to optimize the length of the bit stream."""
		
		# Select the most efficient segment encoding automatically
		if text == "":
			return []
		elif QrSegment.is_numeric(text):
			return [QrSegment.make_numeric(text)]
		elif QrSegment.is_alphanumeric(text):
			return [QrSegment.make_alphanumeric(text)]
		else:
			return [QrSegment.make_bytes(text.encode("UTF-8"))]
	
	
	@staticmethod
	def make_eci(assignval: int) -> QrSegment:
		"""Returns a segment representing an Extended Channel Interpretation
		(ECI) designator with the given assignment value."""
		bb = _BitBuffer()
		if assignval < 0:
			raise ValueError("ECI assignment value out of range")
		elif assignval < (1 << 7):
			bb.append_bits(assignval, 8)
		elif assignval < (1 << 14):
			bb.append_bits(0b10, 2)
			bb.append_bits(assignval, 14)
		elif assignval < 1000000:
			bb.append_bits(0b110, 3)
			bb.append_bits(assignval, 21)
		else:
			raise ValueError("ECI assignment value out of range")
		return QrSegment(QrSegment.Mode.ECI, 0, bb)
	
	
	# Tests whether the given string can be encoded as a segment in numeric mode.
	# A string is encodable iff each character is in the range 0 to 9.
	@staticmethod
	def is_numeric(text: str) -> bool:
		return QrSegment._NUMERIC_REGEX.fullmatch(text) is not None
	
	
	# Tests whether the given string can be encoded as a segment in alphanumeric mode.
	# A string is encodable iff each character is in the following set: 0 to 9, A to Z
	# (uppercase only), space, dollar, percent, asterisk, plus, hyphen, period, slash, colon.
	@staticmethod
	def is_alphanumeric(text: str) -> bool:
		return QrSegment._ALPHANUMERIC_REGEX.fullmatch(text) is not None
	
	
	# ---- Private fields ----
	
	# The mode indicator of this segment. Accessed through get_mode().
	_mode: QrSegment.Mode
	
	# The length of this segment's unencoded data. Measured in characters for
	# numeric/alphanumeric/kanji mode, bytes for byte mode, and 0 for ECI mode.
	# Always zero or positive. Not the same as the data's bit length.
	# Accessed through get_num_chars().
	_numchars: int
	
	# The data bits of this segment. Accessed through get_data().
	_bitdata: list[int]
	
	
	# ---- Constructor (low level) ----
	
	def __init__(self, mode: QrSegment.Mode, numch: int, bitdata: Sequence[int]) -> None:
		"""Creates a new QR Code segment with the given attributes and data.
		The character count (numch) must agree with the mode and the bit buffer length,
		but the constraint isn't checked. The given bit buffer is cloned and stored."""
		if numch < 0:
			raise ValueError()
		self._mode = mode
		self._numchars = numch
		self._bitdata = list(bitdata)  # Make defensive copy
	
	
	# ---- Accessor methods ----
	
	def get_mode(self) -> QrSegment.Mode:
		"""Returns the mode field of this segment."""
		return self._mode
	
	def get_num_chars(self) -> int:
		"""Returns the character count field of this segment."""
		return self._numchars
	
	def get_data(self) -> list[int]:
		"""Returns a new copy of the data bits of this segment."""
		return list(self._bitdata)  # Make defensive copy
	
	
	# Package-private function
	@staticmethod
	def get_total_bits(segs: Sequence[QrSegment], version: int) -> Optional[int]:
		"""Calculates the number of bits needed to encode the given segments at
		the given version. Returns a non-negative number if successful. Otherwise
		returns None if a segment has too many characters to fit its length field."""
		result = 0
		for seg in segs:
			ccbits: int = seg.get_mode().num_char_count_bits(version)
			if seg.get_num_chars() >= (1 << ccbits):
				return None  # The segment's length doesn't fit the field's bit width
			result += 4 + ccbits + len(seg._bitdata)
		return result
	
	
	# ---- Constants ----
	
	# Describes precisely all strings that are encodable in numeric mode.
	_NUMERIC_REGEX: re.Pattern[str] = re.compile(r"[0-9]*")
	
	# Describes precisely all strings that are encodable in alphanumeric mode.
	_ALPHANUMERIC_REGEX: re.Pattern[str] = re.compile(r"[A-Z0-9 $%*+./:-]*")
	
	# Dictionary of "0"->0, "A"->10, "$"->37, etc.
	_ALPHANUMERIC_ENCODING_TABLE: dict[str,int] = {ch: i for (i, ch) in enumerate("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:")}
	
	
	# ---- Public helper enumeration ----
	
	class Mode:
		"""Describes how a segment's data bits are interpreted. Immutable."""
		
		_modebits: int  # The mode indicator bits, which is a uint4 value (range 0 to 15)
		_charcounts: tuple[int,int,int]  # Number of character count bits for three different version ranges
		
		# Private constructor
		def __init__(self, modebits: int, charcounts: tuple[int,int,int]):
			self._modebits = modebits
			self._charcounts = charcounts
		
		# Package-private method
		def get_mode_bits(self) -> int:
			"""Returns an unsigned 4-bit integer value (range 0 to 15) representing the mode indicator bits for this mode object."""
			return self._modebits
		
		# Package-private method
		def num_char_count_bits(self, ver: int) -> int:
			"""Returns the bit width of the character count field for a segment in this mode
			in a QR Code at the given version number. The result is in the range [0, 16]."""
			return self._charcounts[(ver + 7) // 17]
		
		# Placeholders
		NUMERIC     : QrSegment.Mode
		ALPHANUMERIC: QrSegment.Mode
		BYTE        : QrSegment.Mode
		KANJI       : QrSegment.Mode
		ECI         : QrSegment.Mode
	
	# Public constants. Create them outside the class.
	Mode.NUMERIC      = Mode(0x1, (10, 12, 14))
	Mode.ALPHANUMERIC = Mode(0x2, ( 9, 11, 13))
	Mode.BYTE         = Mode(0x4, ( 8, 16, 16))
	Mode.KANJI        = Mode(0x8, ( 8, 10, 12))
	Mode.ECI          = Mode(0x7, ( 0,  0,  0))



# ---- Private helper class ----

class _BitBuffer(list[int]):
	"""An appendable sequence of bits (0s and 1s). Mainly used by QrSegment."""
	
	def append_bits(self, val: int, n: int) -> None:
		"""Appends the given number of low-order bits of the given
		value to this buffer. Requires n >= 0 and 0 <= val < 2^n."""
		if (n < 0) or (val >> n != 0):
			raise ValueError("Value out of range")
		self.extend(((val >> i) & 1) for i in reversed(range(n)))


def _get_bit(x: int, i: int) -> bool:
	"""Returns true iff the i'th bit of x is set to 1."""
	return (x >> i) & 1 != 0



class DataTooLongError(ValueError):
	"""Raised when the supplied data does not fit any QR Code version. Ways to handle this exception include:
	- Decrease the error correction level if it was greater than Ecc.LOW.
	- If the encode_segments() function was called with a maxversion argument, then increase
	  it if it was less than QrCode.MAX_VERSION. (This advice does not apply to the other
	  factory functions because they search all versions up to QrCode.MAX_VERSION.)
	- Split the text data into better or optimal segments in order to reduce the number of bits required.
	- Change the text or binary data to be shorter.
	- Change the text to fit the character set of a particular segment mode (e.g. alphanumeric).
	- Propagate the error upward to the caller/user."""
	pass
# ===== 以下都是本脚本自己的代码 =====

import argparse
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path


# ---------------------------------------------------------------- 小工具
def say(msg: str = "") -> None:
    print(msg, flush=True)


def _log(msg: str) -> None:
    """把关键动作和报错记到 ~/.bili-hardcore/run.log，方便出问题时排查。"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_DIR / "run.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def open_file(path: Path) -> None:
    """在 Windows 上用默认看图程序打开文件；打不开就只把路径告诉用户。"""
    try:
        os.startfile(str(path))  # noqa: S606
    except Exception:
        say(f"（自动打开失败也没事，文件在这里：{path}）")


# ------------------------------------------------- B 站接口层
# 以下接口实现移植自 Karben233/bili-hardcore 的 bili_quiz.py（MIT License）
APPKEY = "783bbb7264451d82"
APPSEC = "2653583c8873dea268ab9386918b1d65"
TICKET_HMAC_KEY = "XgwSnGZ1p"

BASE_API = "https://api.bilibili.com"
CONFIG_DIR = Path.home() / ".bili-hardcore"
AUTH_PATH = CONFIG_DIR / "auth.json"
CAPTCHA_IMG_PATH = CONFIG_DIR / "captcha.jpg"
LOGIN_QR_PATH = CONFIG_DIR / "login_qr.png"
SELFTEST_QR_PATH = CONFIG_DIR / "selftest_qr.png"
AUTH_MAX_AGE_SECONDS = 7 * 24 * 3600

APP_HEADERS = {
    "User-Agent": "Mozilla/5.0 BiliDroid/1.12.0 (bbcallen@gmail.com)",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "x-bili-metadata-legal-region": "CN",
    "x-bili-aurora-eid": "",
    "x-bili-aurora-zone": "",
}
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)


def appsign(params: list) -> None:
    """B 站客户端接口的签名：追加 ts/appkey -> 排序 -> urlencode -> MD5(+appsec)。"""
    params.append(("ts", str(int(time.time()))))
    params.append(("appkey", APPKEY))
    params.sort(key=lambda kv: kv[0])
    query = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in params
    )
    params.append(("sign", hashlib.md5((query + APPSEC).encode("utf-8")).hexdigest()))


def gen_ticket_params() -> list:
    ts = str(int(time.time()))
    hexsign = hmac.new(
        TICKET_HMAC_KEY.encode("utf-8"), f"ts{ts}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return [("key_id", "ec02"), ("hexsign", hexsign), ("context[ts]", ts), ("csrf", "")]


def _request(method: str, url: str, *, query=None, form=None, auth=None,
             browser_ua: bool = False, timeout: int = 30) -> dict:
    headers = dict(APP_HEADERS)
    if browser_ua:
        headers["User-Agent"] = BROWSER_UA
    if auth:
        if auth.get("mid"):
            headers["x-bili-mid"] = auth["mid"]
        if auth.get("cookie"):
            headers["cookie"] = auth["cookie"]
        if auth.get("_ticket"):
            headers["x-bili-ticket"] = auth["_ticket"]

    final_url = url
    data = None
    if query:
        final_url = f"{url}?{urllib.parse.urlencode(query)}"
    if form:
        data = urllib.parse.urlencode(form).encode("utf-8")

    req = urllib.request.Request(final_url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"B 站返回 HTTP {e.code}：{body[:300]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络请求失败：{e}") from None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f"B 站返回的不是 JSON：{body[:300]}") from None


def signed_get(url: str, params: list, auth=None) -> dict:
    appsign(params)
    return _request("GET", url, query=params, auth=auth)


def signed_post(url: str, params: list, auth=None) -> dict:
    appsign(params)
    return _request("POST", url, form=params, auth=auth)


# ------------------------------------------------- 登录凭证（存在本机）
def load_auth():
    if not AUTH_PATH.exists():
        return None
    try:
        if time.time() - AUTH_PATH.stat().st_mtime > AUTH_MAX_AGE_SECONDS:
            return None
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        return data if data.get("access_token") else None
    except (OSError, json.JSONDecodeError):
        return None


def save_auth(auth: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(json.dumps(auth, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_auth() -> None:
    if AUTH_PATH.exists():
        AUTH_PATH.unlink()


def _auth_with_ticket():
    auth = load_auth()
    if not auth:
        return None
    try:
        t = ticket_get()
        if t.get("ok"):
            auth["_ticket"] = t["ticket"]
    except RuntimeError:
        pass
    return auth


# ------------------------------------------------- B 站各接口
def ticket_get() -> dict:
    params = gen_ticket_params()
    url = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
    resp = _request("POST", url, query=params, browser_ua=True)
    ticket = (resp.get("data") or {}).get("ticket")
    return {"ok": bool(ticket), "ticket": ticket or "", "raw": resp}


def _pre_auth() -> dict:
    try:
        t = ticket_get()
        if t.get("ok"):
            return {"_ticket": t["ticket"]}
    except RuntimeError:
        pass
    return {}


def qrcode_get() -> dict:
    resp = signed_post(
        "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code",
        [("local_id", "0")],
        auth=_pre_auth(),
    )
    if resp.get("code") != 0:
        return {"ok": False, "raw": resp}
    data = resp.get("data") or {}
    return {"ok": True, "url": data.get("url", ""), "auth_code": data.get("auth_code", "")}


def qrcode_poll(auth_code: str) -> dict:
    resp = signed_post(
        "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll",
        [("auth_code", auth_code), ("local_id", "0")],
        auth=_pre_auth(),
    )
    if resp.get("code") != 0:
        return {"ok": False, "pending": True, "code": resp.get("code"), "raw": resp}
    data = resp.get("data") or {}
    cookies = (data.get("cookie_info") or {}).get("cookies") or []
    parts, csrf = [], ""
    for c in cookies:
        name, value = c.get("name", ""), c.get("value", "")
        if name:
            parts.append(f"{name}={value}")
            if name == "bili_jct":
                csrf = value
    auth = {
        "access_token": data.get("access_token", ""),
        "csrf": csrf,
        "mid": str(data.get("mid", "")),
        "cookie": "; ".join(parts),
    }
    save_auth(auth)
    return {"ok": True, "mid": auth["mid"]}


def level_get(auth) -> dict:
    resp = signed_get(
        "https://app.bilibili.com/x/v2/account/myinfo",
        [("access_key", auth.get("access_token", ""))],
        auth=auth,
    )
    if resp.get("code") != 0:
        return {"ok": False, "raw": resp}
    data = resp.get("data") or {}
    return {"ok": True, "level": data.get("level", 0), "mid": data.get("mid"),
            "name": data.get("name", "")}


def _common_params(auth) -> list:
    return [
        ("access_key", auth.get("access_token", "")),
        ("csrf", auth.get("csrf", "")),
        ("disable_rcmd", "0"),
        ("mobi_app", "android"),
        ("platform", "android"),
        ("statistics", '{"appId":1,"platform":3,"version":"8.40.0","abtest":""}'),
        ("web_location", "333.790"),
    ]


def category_get(auth) -> dict:
    resp = signed_get(f"{BASE_API}/x/senior/v1/category", _common_params(auth), auth=auth)
    if resp.get("code") != 0:
        return {"ok": False, "code": resp.get("code"), "raw": resp}
    cats = [
        {"id": c.get("id", ""), "name": c.get("name", "")}
        for c in ((resp.get("data") or {}).get("categories") or [])
    ]
    return {"ok": True, "categories": cats}


def captcha_get(auth) -> dict:
    resp = signed_get(f"{BASE_API}/x/senior/v1/captcha", _common_params(auth), auth=auth)
    if resp.get("code") != 0:
        return {"ok": False, "raw": resp}
    data = resp.get("data") or {}
    url, token = data.get("url", ""), data.get("token", "")
    img_path = None
    if url:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                CAPTCHA_IMG_PATH.write_bytes(r.read())
            img_path = str(CAPTCHA_IMG_PATH)
        except Exception as e:
            say(f"（验证码图片下载失败：{e}，可以直接看这个网址：{url}）")
    return {"ok": True, "url": url, "token": token, "image_path": img_path}


def captcha_submit(auth, code: str, token: str, ids: str) -> dict:
    params = [
        ("access_key", auth.get("access_token", "")),
        ("csrf", auth.get("csrf", "")),
        ("bili_code", code),
        ("bili_token", token),
        ("disable_rcmd", "0"),
        ("gt_challenge", ""),
        ("gt_seccode", ""),
        ("gt_validate", ""),
        ("ids", ids),
        ("mobi_app", "android"),
        ("platform", "android"),
        ("statistics", '{"appId":1,"platform":3,"version":"8.40.0","abtest":""}'),
        ("type", "bilibili"),
    ]
    resp = signed_post(f"{BASE_API}/x/senior/v1/captcha/submit", params, auth=auth)
    if resp.get("code") != 0:
        return {"ok": False, "raw": resp}
    return question_get(auth)  # 验证码过了，直接带出下一题


def question_get(auth) -> dict:
    resp = signed_get(f"{BASE_API}/x/senior/v1/question", _common_params(auth), auth=auth)
    if resp.get("code") == 0:
        d = resp.get("data") or {}
        answers = [
            {"text": a.get("ans_text", ""), "hash": a.get("ans_hash", "")}
            for a in (d.get("answers") or [])
        ]
        return {
            "ok": True,
            "need_captcha": False,
            "id": d.get("id", 0),
            "question_num": d.get("question_num", 0),
            "question": d.get("question", ""),
            "answers": answers,
        }
    # 非 0：通常是该过验证码了，也可能是今天次数用完
    return {"ok": False, "need_captcha": True, "code": resp.get("code"), "raw": resp}


def answer_submit(auth, qid, ans_hash: str, ans_text: str) -> dict:
    params = _common_params(auth) + [
        ("id", str(qid)),
        ("ans_hash", ans_hash),
        ("ans_text", ans_text),
    ]
    resp = signed_post(f"{BASE_API}/x/senior/v1/answer/submit", params, auth=auth)
    if resp.get("code") != 0:
        # B 站真实错误码：41103=这题已经答过、41104=答得太快、41105=答题完成、41099=次数用尽
        return {"ok": False, "code": resp.get("code"), "raw": resp}
    r = signed_get(f"{BASE_API}/x/senior/v1/answer/result", _common_params(auth), auth=auth)
    if r.get("code") != 0:
        return {"ok": True, "score": 0}
    return {"ok": True, "score": (r.get("data") or {}).get("score", 0)}


def answer_result(auth) -> dict:
    resp = signed_get(f"{BASE_API}/x/senior/v1/answer/result", _common_params(auth), auth=auth)
    if resp.get("code") != 0:
        return {"ok": False, "raw": resp}
    data = resp.get("data") or {}
    scores = [
        {"category": s.get("category", ""), "score": s.get("score", 0), "total": s.get("total", 0)}
        for s in (data.get("scores") or [])
    ]
    return {
        "ok": True,
        "score": data.get("score", 0),
        "scores": scores,
        "pass": bool(data.get("pass")),
        "chance": data.get("chance"),
    }


def answer_exit(auth) -> dict:
    """放弃当前这一局（B 站保留进度，重跑可以接着答）。"""
    resp = signed_post(f"{BASE_API}/x/senior/v1/answer/exit", [("csrf", auth.get("csrf", ""))], auth=auth)
    return {"ok": resp.get("code") == 0, "raw": resp}


def member_info(auth) -> dict:
    """B 站官方口径的“你是不是硬核会员”。"""
    resp = signed_get(f"{BASE_API}/x/senior/v1/member/info", _common_params(auth), auth=auth)
    if resp.get("code") != 0:
        return {"ok": False, "raw": resp}
    data = resp.get("data") or {}
    return {"ok": True, "senior_member": int(data.get("senior_member") or 0) == 1, "raw": resp}


def entry_get(auth) -> dict:
    """能不能参加试炼（Lv6 + 今天还有次数）。"""
    resp = signed_get(f"{BASE_API}/x/senior/v1/entry", _common_params(auth), auth=auth)
    if resp.get("code") != 0:
        return {"ok": False, "raw": resp}
    data = resp.get("data") or {}
    return {"ok": True, "eligible": bool(data.get("eligible")), "stage": data.get("stage"), "raw": resp}


# ------------------------------------------------- 二维码 -> PNG（纯标准库）
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_qr_png(text: str, out_path: Path, scale: int = 8, border: int = 4) -> Path:
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    n = qr.get_size()
    side = (n + 2 * border) * scale
    ihdr = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)  # 8 位灰度 PNG
    raw = bytearray()
    for my in range(side):
        raw.append(0)  # PNG 每行行首要加一个 filter 字节
        for mx in range(side):
            x, y = mx // scale - border, my // scale - border
            dark = 0 <= x < n and 0 <= y < n and qr.get_module(x, y)
            raw.append(0x00 if dark else 0xFF)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png)
    return out_path


# ------------------------------------------------- JEV（TypeSafe System One）
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"


def find_key_file(key_file=None) -> Path:
    """找 JEV key 文件：--key-file > 环境变量 JEV_KEY_FILE > 脚本同目录的 jev.txt"""
    if key_file:
        return Path(key_file)
    env = os.environ.get("JEV_KEY_FILE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "jev.txt"


def load_jev_key(key_file=None) -> str:
    p = find_key_file(key_file)
    if not p.exists():
        raise RuntimeError(
            f"找不到 JEV key 文件：{p}\n"
            "把 key 存成一个文本文件（第一行就是 key），再用下面任一种方式指定：\n"
            "  1) 放到脚本同目录，文件名就叫 jev.txt\n"
            "  2) 命令后面加 --key-file 路径\n"
            "  3) 设置环境变量 JEV_KEY_FILE 指向它"
        )
    text = p.read_text(encoding="utf-8-sig", errors="replace").strip()
    if not text:
        raise RuntimeError(f"JEV key 文件是空的：{p}")
    line = text.splitlines()[0].strip()
    for sep in ("=", ":"):
        if sep in line:
            left, right = line.split(sep, 1)
            if len(left.strip()) <= 20 and right.strip():
                line = right.strip()
                break
    return line


def _jev_request(key: str, payload: dict) -> dict:
    req = urllib.request.Request(
        JEV_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"JEV 返回 HTTP {e.code}：{body[:300]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上 JEV：{e}") from None


def _parse_choice(data: dict, options: list) -> tuple:
    ans = (data.get("answers") or {}).get("answer") or {}
    conf = float(ans.get("confidence") or 0)
    probs = ans.get("probabilities") or {}
    candidates = [str(ans.get("choice") or "").strip()]
    candidates += [str(k).strip() for k, _ in sorted(probs.items(), key=lambda kv: -float(kv[1] or 0))]
    for c in candidates:
        if c.isdigit() and 1 <= int(c) <= len(options):
            return int(c), conf
    for c in candidates:
        for i, opt in enumerate(options, 1):
            if c and (c == opt or c in opt or opt in c):
                return i, conf
    return 1, conf  # 实在认不出来就先选 A，B 站不允许跳题


def jev_choose(key: str, question: str, options: list) -> tuple:
    criteria = {str(i): f"选项{i}：{text}" for i, text in enumerate(options, 1)}
    payload = {
        "state": f"【单选题】{question}",
        "model": JEV_MODEL,
        "questions": {
            "answer": {
                "type": "choice",
                "instructions": "从 criteria 里选出这道单选题的正确答案，只选一个编号。",
                "criteria": criteria,
            }
        },
    }
    last_err = None
    for attempt in range(3):
        try:
            return _parse_choice(_jev_request(key, payload), options)
        except Exception as e:  # 网络抖动就重试
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"JEV 调用失败：{last_err}")


# ------------------------------------------------- 流程
def ensure_login() -> dict:
    auth = load_auth()
    if auth:
        say(f"已登录（B 站账号 mid={auth.get('mid', '')}）。")
        return auth
    say("\n=== 第一次使用，要扫码登录一次（登录态管 7 天）===")
    qr = qrcode_get()
    if not qr.get("ok"):
        raise RuntimeError(f"取登录二维码失败：{qr.get('raw')}")
    make_qr_png(qr["url"], LOGIN_QR_PATH)
    say(f"二维码图片已生成，并尝试用默认看图程序打开：{LOGIN_QR_PATH}")
    say("请用手机 B 站 APP 的“扫一扫”扫它，二维码约 60 秒有效。")
    open_file(LOGIN_QR_PATH)
    for _ in range(30):
        time.sleep(2)
        r = qrcode_poll(qr["auth_code"])
        if r.get("ok"):
            say("登录成功。")
            return load_auth()
    raise RuntimeError("二维码过期了。重新跑一遍脚本，会给你出新的二维码。")


def check_level(auth) -> None:
    auth = _auth_with_ticket() or auth
    lv = level_get(auth)
    if not lv.get("ok"):
        raise RuntimeError(f"查账号等级失败：{lv.get('raw')}")
    level = int(lv.get("level") or 0)
    say(f"账号：{lv.get('name')}（mid={lv.get('mid')}），等级 Lv{level}。")
    if level < 6:
        raise RuntimeError("B 站规定满 6 级才能参加硬核会员试炼，现在还不能答题。")


def pick_categories(auth) -> str:
    cats = category_get(auth)
    if not cats.get("ok"):
        if cats.get("code") == 41099:
            raise RuntimeError("今天的答题次数用完了（每天 3 次），明天再来。")
        raise RuntimeError(f"取答题分区失败：{cats.get('raw')}")
    items = cats.get("categories") or []
    if not items:
        return ""
    say("\n请选择答题分区（输入编号，1~3 个，用逗号分开）：")
    for i, c in enumerate(items, 1):
        say(f"  {i}) {c.get('name')}")
    while True:
        raw = input("你的选择（例如 1,2）：").replace("，", ",").strip()
        try:
            idxs = [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            idxs = []
        idxs = list(dict.fromkeys(idxs))
        if 1 <= len(idxs) <= 3 and all(1 <= i <= len(items) for i in idxs):
            return ",".join(str(items[i - 1].get("id")) for i in idxs)
        say("输入不对：至少选 1 个，最多选 3 个。")


def start_attempt(auth) -> dict:
    """按 B 站的正规顺序开一局新的：选分区 -> 取验证码 -> 交验证码。

    交完验证码这一步，B 站才算正式开始新的一局（会占用今天 1 次机会），
    并直接返回第一道题。
    """
    ids = pick_categories(auth)
    for _ in range(3):
        cap = captcha_get(auth)
        if not cap.get("ok"):
            code = (cap.get("raw") or {}).get("code")
            if code == 41099:
                raise RuntimeError("今天的 3 次挑战机会已经用完，明天再来。")
            raise RuntimeError(f"取验证码失败：{cap.get('raw')}")
        say("\n=== 过验证码（过了才正式开始）===")
        say(f"验证码图片：{cap.get('image_path') or cap.get('url')}")
        if cap.get("image_path"):
            open_file(Path(cap["image_path"]))
        code_txt = input("请看图，输入验证码里的文字：").strip()
        r = captcha_submit(auth, code_txt, cap.get("token", ""), ids)
        if r.get("ok"):
            return r
        raw = r.get("raw") or {}
        _log(f"captcha_submit failed raw={raw}")
        if raw.get("code") == -105:
            say("验证码不对，换一张再来。")
            continue
        if raw.get("code") == 41099:
            raise RuntimeError("今天的 3 次挑战机会已经用完，明天再来。")
        say(f"验证码提交失败：{raw}")
    raise RuntimeError("验证码连续失败 3 次，先停一停，过会儿再跑。")


def print_account_state(auth) -> dict:
    """把 B 站那边的真实状态打出来：是不是硬核会员、今天还剩几次、上次成绩。"""
    auth = _auth_with_ticket() or auth
    state = {}
    lv = level_get(auth)
    if lv.get("ok"):
        say(f"账号：{lv.get('name')}（mid={lv.get('mid')}），等级 Lv{lv.get('level')}")
    mi = member_info(auth)
    state["member"] = mi
    if mi.get("ok"):
        say(f"硬核会员：{'是' if mi.get('senior_member') else '否'}")
    res = answer_result(auth)
    state["result"] = res
    if res.get("ok"):
        if res.get("chance") is not None:
            say(f"今日剩余挑战次数：{res.get('chance')}")
        if res.get("scores"):
            prev = "、".join(
                f"{s.get('category')} {s.get('score')}/{s.get('total')}" for s in res["scores"]
            )
            say(f"上一次答题记录：{prev}（{'已通过' if res.get('pass') else '未通过'}）")
    en = entry_get(auth)
    state["entry"] = en
    if en.get("ok"):
        say(f"当前可否挑战：{'可以' if en.get('eligible') else '不可以'}")
    return state


def show_result(auth) -> None:
    auth = _auth_with_ticket() or auth
    r = answer_result(auth)
    if not r.get("ok"):
        say(f"查分失败：{r.get('raw')}")
        return
    say("\n===== 答题结果 =====")
    say(f"总分：{r.get('score')}")
    for s in r.get("scores") or []:
        say(f"{s.get('category')}：{s.get('score')}/{s.get('total')}")
    if r.get("chance") is not None:
        say(f"今日剩余挑战次数：{r.get('chance')}")
    mi = member_info(auth)
    if mi.get("ok"):
        say(f"硬核会员：{'是（B 站已认定）' if mi.get('senior_member') else '否（还没生效或未通过）'}")
    say("====================")


def pick_in_progress(auth):
    """看有没有一局没答完（题号 1~100）。题号 >100 是上一局留下的残留题，不算。"""
    q = question_get(auth)
    if q.get("ok") and 1 <= int(q.get("question_num") or 0) <= 100:
        return q
    return None


def run_quiz(auth, key: str) -> None:
    say("\n=== 账号状态（B 站实时返回）===")
    state = print_account_state(auth)
    if (state.get("member") or {}).get("senior_member"):
        say("\nB 站显示你现在就是硬核会员（有效期 365 天），不用再答了。")
        return
    if (state.get("entry") or {}).get("ok") and not state["entry"].get("eligible"):
        say("\nB 站显示你当前不能参加试炼（不是 Lv6，或今天的次数已用完）。")
        return
    q = pick_in_progress(auth)
    if q:
        say(f"\n检测到上一局没答完（当前第 {q.get('question_num')} 题），接着答不另扣次数。")
        if input("回车接着这一局，输入 n 放弃它重开一局：").strip().lower() == "n":
            r = answer_exit(auth)
            if r.get("ok"):
                say("已放弃上一局。")
                q = None
            else:
                say(f"放弃失败（{r.get('raw')}），那就接着这一局答。")
    if q is None:
        say("\n=== 开始答题 ===")
        say("规则：120 分钟内答对 60 题及以上就算通过，最多答 100 题；每天最多 3 次机会。")
        if input("按回车开始（会消耗今天 1 次机会），输入 n 取消：").strip().lower() == "n":
            say("已取消，没消耗答题次数。")
            return
        q = start_attempt(auth)
        _log(f"attempt started: first question num={q.get('question_num')} id={q.get('id')}")
    else:
        say(f"好，接着第 {q.get('question_num')} 题往下答。")
    answered = 0
    dup = 0
    bad = 0
    while True:
        if q is None:
            q = question_get(_auth_with_ticket() or auth)
        if not q.get("ok"):
            code = q.get("code")
            _log(f"question_get failed code={code} raw={q.get('raw')}")
            if code == 41105:
                say("B 站说这一局答完了。")
                break
            bad += 1
            if bad >= 3:
                say(f"连续 3 次取题失败（错误码 {code}），先停下来。")
                break
            time.sleep(2)
            q = None
            continue
        bad = 0
        num = int(q.get("question_num") or 0)
        question = q.get("question", "")
        answers = q.get("answers") or []
        if not answers:
            break
        options = [a.get("text", "") for a in answers]
        try:
            idx, conf = jev_choose(key, question, options)
        except Exception as e:
            say(f"JEV 没答上（{e}），这题请你手动选：")
            for i, opt in enumerate(options, 1):
                say(f"  {i}) {opt}")
            idx = int(input("输入编号：").strip())
            conf = 0.0
        ans = answers[idx - 1]
        r = answer_submit(_auth_with_ticket() or auth, q.get("id"), ans.get("hash", ""), ans.get("text", ""))
        if not r.get("ok"):
            code = r.get("code")
            _log(f"submit failed code={code} raw={r.get('raw')}")
            if code == 41104:
                say("B 站说答太快了，等 3 秒重交这题。")
                time.sleep(3)
                continue
            if code == 41103:
                dup += 1
                if dup >= 3:
                    raise RuntimeError(
                        "连续 3 题被 B 站判成“这题已经答过”。日志在 "
                        + str(CONFIG_DIR / "run.log")
                        + "，发我看看。"
                    )
                q = None
                continue
            if code == 41105:
                say("B 站说这一局答完了。")
                break
            if code == 41099:
                raise RuntimeError("今天的 3 次机会已经用完，明天再来。")
            raise RuntimeError(f"提交答案失败：{r.get('raw')}")
        dup = 0
        answered += 1
        say(f"[第{num}题] 选{idx}（{ans.get('text')}）把握度{conf:.0%}，累计答对 {r.get('score')}")
        if num >= 100:
            break
        q = None
    say(f"\n这一局共提交 {answered} 题。")
    show_result(auth)


def selftest(key_file=None) -> None:
    say("=== 自检 1/3：读 JEV key ===")
    key = load_jev_key(key_file)
    say(f"key 读到了：{key[:8]}…（共 {len(key)} 个字符）")
    say("=== 自检 2/3：生成二维码 PNG ===")
    p = make_qr_png("https://example.com/selftest", SELFTEST_QR_PATH)
    head = p.read_bytes()[:8]
    if head != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("生成的二维码 PNG 文件头不对。")
    say(f"正常：{p}（{p.stat().st_size} 字节）")
    say("=== 自检 3/3：JEV 试答一题（1 次很小的调用）===")
    idx, conf = jev_choose(key, "中国的首都是哪里？", ["巴黎", "北京", "伦敦", "东京"])
    say(f"JEV 选了第 {idx} 个（把握度 {conf:.0%}），样题标准答案是第 2 个。")
    if idx == 2:
        say("自检全部通过，可以正式跑了。")
    else:
        say("注意：样题没答对，正式跑之前最好先看看 JEV 的状态。")


def _pause_if_interactive() -> None:
    try:
        if sys.stdin.isatty():
            input("\n按回车退出...")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="B 站硬核会员 JEV 自动答题（单文件）")
    parser.add_argument("--selftest", action="store_true", help="自检（测 key 和二维码），不动 B 站账号")
    parser.add_argument("--status", action="store_true", help="看登录状态和账号等级")
    parser.add_argument("--result", action="store_true", help="查分")
    parser.add_argument("--logout", action="store_true", help="退出登录")
    parser.add_argument("--key-file", default=None, help="JEV key 文件路径（默认找脚本同目录的 jev.txt）")
    args = parser.parse_args()

    try:
        if args.selftest:
            selftest(args.key_file)
        elif args.status:
            auth = load_auth()
            if not auth:
                say("还没登录（或者登录已过期）。跑一次主流程会带你扫码。")
            else:
                print_account_state(auth)
        elif args.result:
            auth = _auth_with_ticket()
            if not auth:
                say("还没登录（或者登录已过期）。")
            else:
                show_result(auth)
        elif args.logout:
            delete_auth()
            say("已退出登录，本机登录凭证已删除。")
        else:
            key = load_jev_key(args.key_file)
            auth = ensure_login()
            check_level(auth)
            run_quiz(auth, key)
    except KeyboardInterrupt:
        say("\n已中断。注意：已经开始的那一次答题机会 B 站会算作用掉。")
    except Exception as e:
        say(f"\n出错了：{e}")
        return 1
    finally:
        _pause_if_interactive()
    return 0


if __name__ == "__main__":
    sys.exit(main())
