#include <mgba/core/core.h>
#include <mgba/core/interface.h>
#include <mgba/core/log.h>
#include <mgba/core/serialize.h>
#include <mgba/internal/gba/input.h>
#include <mgba/internal/gba/gba.h>
#include <mgba/internal/gba/memory.h>
#include <mgba-util/vfs.h>

#include <stdbool.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct mCore* core = NULL;
static color_t* video_buffer = NULL;
static unsigned video_width = 0;
static unsigned video_height = 0;
static struct mStandardLogger logger;
static struct mAVStream capture_stream;
static FILE* capture_video = NULL;
static FILE* capture_audio = NULL;
static unsigned capture_audio_rate = 0;
static uint64_t capture_video_frames = 0;
static uint64_t capture_audio_frames = 0;

static void capture_dimensions_changed(struct mAVStream* stream, unsigned width, unsigned height) {
	(void) stream;
	video_width = width;
	video_height = height;
}

static void capture_audio_rate_changed(struct mAVStream* stream, unsigned rate) {
	(void) stream;
	capture_audio_rate = rate;
}

static void capture_video_frame(struct mAVStream* stream, const color_t* pixels, size_t stride) {
	(void) stream;
	if (!capture_video || !pixels) return;
	uint8_t* row = malloc((size_t) video_width * 4);
	if (!row) return;
	for (unsigned y = 0; y < video_height; ++y) {
		for (unsigned x = 0; x < video_width; ++x) {
			color_t pixel = pixels[(size_t) y * stride + x];
			row[x * 4 + 0] = (uint8_t) (pixel & 0xFF);
			row[x * 4 + 1] = (uint8_t) ((pixel >> 8) & 0xFF);
			row[x * 4 + 2] = (uint8_t) ((pixel >> 16) & 0xFF);
			row[x * 4 + 3] = 0xFF;
		}
		fwrite(row, (size_t) video_width * 4, 1, capture_video);
	}
	free(row);
	++capture_video_frames;
}

static void capture_audio_frame(struct mAVStream* stream, int16_t left, int16_t right) {
	(void) stream;
	if (!capture_audio) return;
	int16_t stereo[2] = {left, right};
	fwrite(stereo, sizeof(stereo), 1, capture_audio);
	++capture_audio_frames;
}

static void stop_capture(void) {
	if (core) core->setAVStream(core, NULL);
	if (capture_video) fclose(capture_video);
	if (capture_audio) fclose(capture_audio);
	capture_video = NULL;
	capture_audio = NULL;
}

static bool start_capture(const char* video_path, const char* audio_path) {
	stop_capture();
	capture_video = fopen(video_path, "wb");
	capture_audio = fopen(audio_path, "wb");
	if (!capture_video || !capture_audio) {
		stop_capture();
		return false;
	}
	capture_audio_rate = 0;
	capture_video_frames = 0;
	capture_audio_frames = 0;
	memset(&capture_stream, 0, sizeof(capture_stream));
	capture_stream.videoDimensionsChanged = capture_dimensions_changed;
	capture_stream.audioRateChanged = capture_audio_rate_changed;
	capture_stream.postVideoFrame = capture_video_frame;
	capture_stream.postAudioFrame = capture_audio_frame;
	core->setAVStream(core, &capture_stream);
	return true;
}

static int button_bit(const char* button) {
	if (!strcmp(button, "A")) return 1 << GBA_KEY_A;
	if (!strcmp(button, "B")) return 1 << GBA_KEY_B;
	if (!strcmp(button, "SELECT")) return 1 << GBA_KEY_SELECT;
	if (!strcmp(button, "START")) return 1 << GBA_KEY_START;
	if (!strcmp(button, "RIGHT")) return 1 << GBA_KEY_RIGHT;
	if (!strcmp(button, "LEFT")) return 1 << GBA_KEY_LEFT;
	if (!strcmp(button, "UP")) return 1 << GBA_KEY_UP;
	if (!strcmp(button, "DOWN")) return 1 << GBA_KEY_DOWN;
	if (!strcmp(button, "R")) return 1 << GBA_KEY_R;
	if (!strcmp(button, "L")) return 1 << GBA_KEY_L;
	return 0;
}

static bool parse_u32_arg(const char* line, uint32_t* out) {
	char command[32] = {0};
	char value[64] = {0};
	if (sscanf(line, "%31s %63s", command, value) != 2) return false;
	char* end = NULL;
	unsigned long parsed = strtoul(value, &end, 0);
	if (!end || *end != '\0') return false;
	*out = (uint32_t) parsed;
	return true;
}

static bool parse_two_u32_args(const char* line, uint32_t* first, uint32_t* second) {
	char command[32] = {0};
	char value1[64] = {0};
	char value2[64] = {0};
	if (sscanf(line, "%31s %63s %63s", command, value1, value2) != 3) return false;
	char* end = NULL;
	unsigned long parsed1 = strtoul(value1, &end, 0);
	if (!end || *end != '\0') return false;
	end = NULL;
	unsigned long parsed2 = strtoul(value2, &end, 0);
	if (!end || *end != '\0') return false;
	*first = (uint32_t) parsed1;
	*second = (uint32_t) parsed2;
	return true;
}

static void advance_frames(int frames) {
	for (int i = 0; i < frames; ++i) {
		core->runFrame(core);
	}
}

static const char base64_table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static char* base64_encode(const uint8_t* data, size_t length) {
	size_t encoded_length = 4 * ((length + 2) / 3);
	char* encoded = malloc(encoded_length + 1);
	if (!encoded) return NULL;

	size_t in = 0;
	size_t out = 0;
	while (in < length) {
		uint32_t octet_a = in < length ? data[in++] : 0;
		uint32_t octet_b = in < length ? data[in++] : 0;
		uint32_t octet_c = in < length ? data[in++] : 0;
		uint32_t triple = (octet_a << 16) | (octet_b << 8) | octet_c;

		encoded[out++] = base64_table[(triple >> 18) & 0x3F];
		encoded[out++] = base64_table[(triple >> 12) & 0x3F];
		encoded[out++] = base64_table[(triple >> 6) & 0x3F];
		encoded[out++] = base64_table[triple & 0x3F];
	}

	size_t padding = (3 - (length % 3)) % 3;
	for (size_t i = 0; i < padding; ++i) {
		encoded[encoded_length - 1 - i] = '=';
	}
	encoded[encoded_length] = '\0';
	return encoded;
}

static int base64_value(char c) {
	if (c >= 'A' && c <= 'Z') return c - 'A';
	if (c >= 'a' && c <= 'z') return c - 'a' + 26;
	if (c >= '0' && c <= '9') return c - '0' + 52;
	if (c == '+') return 62;
	if (c == '/') return 63;
	return -1;
}

/* Decode base64 in-place into a freshly allocated buffer. Returns NULL on bad input. */
static uint8_t* base64_decode(const char* input, size_t* out_length) {
	size_t in_len = strlen(input);
	while (in_len > 0 && (input[in_len - 1] == '=' || input[in_len - 1] == '\r' || input[in_len - 1] == '\n')) {
		--in_len;
	}
	size_t out_cap = in_len / 4 * 3 + 3;
	uint8_t* out = malloc(out_cap ? out_cap : 1);
	if (!out) return NULL;

	size_t out_len = 0;
	uint32_t buffer = 0;
	int bits = 0;
	for (size_t i = 0; i < in_len; ++i) {
		int value = base64_value(input[i]);
		if (value < 0) {
			free(out);
			return NULL;
		}
		buffer = (buffer << 6) | (uint32_t) value;
		bits += 6;
		if (bits >= 8) {
			bits -= 8;
			out[out_len++] = (uint8_t) ((buffer >> bits) & 0xFF);
		}
	}
	*out_length = out_len;
	return out;
}

static char* screenshot_rgba_base64(void) {
	if (!video_buffer || !video_width || !video_height) return NULL;
	size_t pixel_count = (size_t) video_width * video_height;
	size_t rgba_length = pixel_count * 4;
	uint8_t* rgba = malloc(rgba_length);
	if (!rgba) return NULL;

	for (size_t i = 0; i < pixel_count; ++i) {
		color_t pixel = video_buffer[i];
		rgba[i * 4 + 0] = (uint8_t) (pixel & 0xFF);
		rgba[i * 4 + 1] = (uint8_t) ((pixel >> 8) & 0xFF);
		rgba[i * 4 + 2] = (uint8_t) ((pixel >> 16) & 0xFF);
		rgba[i * 4 + 3] = 0xFF;
	}

	char* encoded = base64_encode(rgba, rgba_length);
	free(rgba);
	return encoded;
}

static bool load_state_path(const char* path) {
	const char* extension = strrchr(path, '.');
	if (extension && !strcmp(extension, ".sav")) {
		bool save_ok = mCoreLoadSaveFile(core, path, true);
		core->reset(core);
		advance_frames(1);
		return save_ok;
	}
	struct VFile* vf = VFileOpen(path, O_RDONLY);
	if (!vf) return false;
	bool ok = mCoreLoadStateNamed(core, vf, SAVESTATE_ALL);
	vf->close(vf);
	return ok;
}

static bool save_state_path(const char* path) {
	struct VFile* vf = VFileOpen(path, O_WRONLY | O_CREAT | O_TRUNC);
	if (!vf) return false;
	bool ok = mCoreSaveStateNamed(core, vf, SAVESTATE_ALL);
	vf->close(vf);
	return ok;
}

static bool initialize_core(const char* rom_path, const char* state_path) {
	core = mCoreFind(rom_path);
	if (!core) {
		fprintf(stderr, "mCoreFind failed for ROM: %s\n", rom_path);
		return false;
	}
	if (!core->init(core)) {
		fprintf(stderr, "core->init failed\n");
		return false;
	}
	mCoreInitConfig(core, "pokebattle-solver");
	mCoreLoadConfig(core);
	if (!mCoreLoadFile(core, rom_path)) {
		fprintf(stderr, "mCoreLoadFile failed for ROM: %s\n", rom_path);
		return false;
	}
	core->desiredVideoDimensions(core, &video_width, &video_height);
	video_buffer = calloc((size_t) video_width * video_height, sizeof(*video_buffer));
	if (!video_buffer) {
		fprintf(stderr, "failed to allocate video buffer\n");
		return false;
	}
	core->setVideoBuffer(core, video_buffer, video_width);
	/* Reset initializes timing event callbacks before savestate deserialization. */
	core->reset(core);
	if (!load_state_path(state_path)) {
		struct GBA* gba = core->board;
		struct GBACartridge* cart = (struct GBACartridge*) gba->memory.rom;
		fprintf(stderr, "mCoreLoadStateNamed failed for state: %s (stateSize=%zu biosChecksum=%08X cartTitle=%.12s cartId=%.4s romCrc=%08X romSize=%zu)\n", state_path, core->stateSize(core), gba->biosChecksum, cart ? cart->title : "none", cart ? (char*) &cart->id : "none", gba->romCrc32, gba->memory.romSize);
		return false;
	}
	/* The desktop emulator config may be muted for fast-forward play. Recording
	 * must contain the cartridge's actual soundtrack regardless of that UI-only
	 * preference. Savestates can also restore the old zero master volume. */
	core->opts.mute = false;
	core->opts.volume = 0x100;
	((struct GBA*) core->board)->audio.masterVolume = 0x100;
	return true;
}

static void deinitialize_core(void) {
	if (!core) return;
	stop_capture();
	core->unloadROM(core);
	core->deinit(core);
	free(video_buffer);
	core = NULL;
	video_buffer = NULL;
	video_width = 0;
	video_height = 0;
}

static void handle_line(char* line) {
	char command[32] = {0};
	sscanf(line, "%31s", command);

	if (!strcmp(command, "LOADSTATE")) {
		char* path = line + strlen(command);
		while (*path == ' ' || *path == '\t') ++path;
		path[strcspn(path, "\r\n")] = '\0';
		printf("%s\n", load_state_path(path) ? "OK" : "ERR savestate load failed");
	} else if (!strcmp(command, "SAVESTATE")) {
		char* path = line + strlen(command);
		while (*path == ' ' || *path == '\t') ++path;
		path[strcspn(path, "\r\n")] = '\0';
		printf("%s\n", save_state_path(path) ? "OK" : "ERR savestate save failed");
	} else if (!strcmp(command, "KEY")) {
		char button[32] = {0};
		int frames = 0;
		if (sscanf(line, "%*s %31s %d", button, &frames) != 2 || frames < 1) {
			printf("ERR usage: KEY BUTTON FRAMES\n");
		} else {
			int bit = button_bit(button);
			if (!bit) {
				printf("ERR unknown key\n");
			} else {
				core->addKeys(core, (uint32_t) bit);
				advance_frames(frames);
				core->clearKeys(core, (uint32_t) bit);
				advance_frames(1);
				printf("OK\n");
			}
		}
	} else if (!strcmp(command, "ADVANCE")) {
		int frames = 0;
		if (sscanf(line, "%*s %d", &frames) != 1 || frames < 0) {
			printf("ERR usage: ADVANCE FRAMES\n");
		} else {
			advance_frames(frames);
			printf("OK\n");
		}
	} else if (!strcmp(command, "READ8")) {
		uint32_t address = 0;
		if (!parse_u32_arg(line, &address)) {
			printf("ERR usage: READ8 ADDRESS\n");
			fflush(stdout);
			return;
		}
		printf("OK %u\n", core->busRead8(core, address) & 0xFF);
	} else if (!strcmp(command, "READ16")) {
		uint32_t address = 0;
		if (!parse_u32_arg(line, &address)) {
			printf("ERR usage: READ16 ADDRESS\n");
			fflush(stdout);
			return;
		}
		printf("OK %u\n", core->busRead16(core, address) & 0xFFFF);
	} else if (!strcmp(command, "READ32")) {
		uint32_t address = 0;
		if (!parse_u32_arg(line, &address)) {
			printf("ERR usage: READ32 ADDRESS\n");
			fflush(stdout);
			return;
		}
		printf("OK %u\n", core->busRead32(core, address));
	} else if (!strcmp(command, "READBLOCK")) {
		uint32_t address = 0;
		uint32_t length = 0;
		if (!parse_two_u32_args(line, &address, &length) || length > 0x20000) {
			printf("ERR usage: READBLOCK ADDRESS LENGTH\n");
			fflush(stdout);
			return;
		}
		uint8_t* bytes = malloc(length ? length : 1);
		if (!bytes) {
			printf("ERR allocation failed\n");
			fflush(stdout);
			return;
		}
		for (uint32_t i = 0; i < length; ++i) {
			bytes[i] = core->busRead8(core, address + i) & 0xFF;
		}
		char* encoded = base64_encode(bytes, length);
		free(bytes);
		if (!encoded) {
			printf("ERR encoding failed\n");
		} else {
			printf("OK %s\n", encoded);
			free(encoded);
		}
	} else if (!strcmp(command, "WRITE8")) {
		uint32_t address = 0;
		uint32_t value = 0;
		if (!parse_two_u32_args(line, &address, &value)) {
			printf("ERR usage: WRITE8 ADDRESS VALUE\n");
		} else {
			core->busWrite8(core, address, (uint8_t) (value & 0xFF));
			printf("OK\n");
		}
	} else if (!strcmp(command, "WRITE16")) {
		uint32_t address = 0;
		uint32_t value = 0;
		if (!parse_two_u32_args(line, &address, &value)) {
			printf("ERR usage: WRITE16 ADDRESS VALUE\n");
		} else {
			core->busWrite16(core, address, (uint16_t) (value & 0xFFFF));
			printf("OK\n");
		}
	} else if (!strcmp(command, "WRITE32")) {
		uint32_t address = 0;
		uint32_t value = 0;
		if (!parse_two_u32_args(line, &address, &value)) {
			printf("ERR usage: WRITE32 ADDRESS VALUE\n");
		} else {
			core->busWrite32(core, address, value);
			printf("OK\n");
		}
	} else if (!strcmp(command, "WRITEBLOCK")) {
		char address_text[64] = {0};
		char payload[4096] = {0};
		if (sscanf(line, "%*s %63s %4095s", address_text, payload) != 2) {
			printf("ERR usage: WRITEBLOCK ADDRESS BASE64\n");
		} else {
			char* end = NULL;
			unsigned long address = strtoul(address_text, &end, 0);
			if (!end || *end != '\0') {
				printf("ERR bad address\n");
			} else {
				size_t length = 0;
				uint8_t* bytes = base64_decode(payload, &length);
				if (!bytes) {
					printf("ERR bad base64\n");
				} else {
					for (size_t i = 0; i < length; ++i) {
						core->busWrite8(core, (uint32_t) address + (uint32_t) i, bytes[i]);
					}
					free(bytes);
					printf("OK %zu\n", length);
				}
			}
		}
	} else if (!strcmp(command, "SCREENSHOT")) {
		char* encoded = screenshot_rgba_base64();
		if (!encoded) {
			printf("ERR screenshot failed\n");
		} else {
			printf("OK %u %u %s\n", video_width, video_height, encoded);
			free(encoded);
		}
	} else if (!strcmp(command, "STARTRECORD")) {
		char video_path[1024] = {0};
		char audio_path[1024] = {0};
		if (sscanf(line, "%*s %1023s %1023s", video_path, audio_path) != 2) {
			printf("ERR usage: STARTRECORD VIDEO_RAW AUDIO_RAW\n");
		} else {
			printf("%s\n", start_capture(video_path, audio_path) ? "OK" : "ERR recording open failed");
		}
	} else if (!strcmp(command, "STOPRECORD")) {
		stop_capture();
		printf("OK %u %llu %llu\n", capture_audio_rate,
			(unsigned long long) capture_video_frames,
			(unsigned long long) capture_audio_frames);
	} else if (!strcmp(command, "MAXSPEED")) {
		printf("OK\n");
	} else if (!strcmp(command, "QUIT")) {
		printf("OK\n");
		fflush(stdout);
		deinitialize_core();
		exit(0);
	} else {
		printf("ERR unknown command\n");
	}
	fflush(stdout);
}

int main(int argc, char** argv) {
	if (argc != 3) {
		fprintf(stderr, "usage: %s ROM_PATH SAVE_STATE_PATH\n", argv[0]);
		return 2;
	}
	mStandardLoggerInit(&logger);
	logger.logToStdout = false;
	mLogSetDefaultLogger(&logger.d);
	if (!initialize_core(argv[1], argv[2])) {
		fprintf(stderr, "failed to initialize libmgba core\n");
		deinitialize_core();
		return 1;
	}

	printf("OK READY\n");
	fflush(stdout);

	char line[4096];
	while (fgets(line, sizeof(line), stdin)) {
		handle_line(line);
	}
	deinitialize_core();
	mStandardLoggerDeinit(&logger);
	return 0;
}
