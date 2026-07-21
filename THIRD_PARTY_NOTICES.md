# Third-party notices

The root `LICENSE` applies only to original project code. Files copied from or linked against other projects remain under their original terms.

## mGBA

The emulator bridge links against mGBA. A local workspace may also contain an mGBA source archive or build tree used to compile that bridge.

- Project: [mGBA](https://github.com/mgba-emu/mgba)
- Copyright: 2013-2026 Jeffrey Pfau and mGBA contributors
- License: [Mozilla Public License 2.0](https://www.mozilla.org/MPL/2.0/)

mGBA is not relicensed under this project's MIT License. If you distribute mGBA source, libraries, or binaries with this project, keep its license notices and comply with the MPL 2.0 source-availability requirements. The upstream source archive contains its full `LICENSE` file.

The original `emulator/mgba_bridge.c` integration code is part of this project. A compiled bridge dynamically links to `libmgba`; build it locally instead of committing the generated executable.

## Pokemon damage calculator and Run & Bun modifications

Files under `web/static/rnbcalc/` derive from the Smogon Pokemon damage calculator and the Run & Bun calculator fork credited in the embedded UI.

- Upstream: [smogon/damage-calc](https://github.com/smogon/damage-calc)
- Run & Bun fork: [SylmarDev/syl-rnb-calc](https://github.com/SylmarDev/syl-rnb-calc)
- License: MIT

The upstream/fork notice is:

```text
The MIT License (MIT)

Copyright (c) 2013-2023 Honko and other contributors
Copyright (c) 2024-2026 SylmarDev and other contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The embedded calculator also includes browser libraries such as jQuery, Select2, and normalize.css. Their existing header notices remain in the vendored files.

## Pokemon battle data

`data/calc_data.json` records its source as `@pkmn/data`. The repository also contains trainer and mechanics data assembled for Pokemon Run & Bun interoperability. Pokemon names, move names, character names, audiovisual material, and game data are not granted under this project's MIT License.

This project does not claim ownership of Pokemon, Pokemon Emerald, Game Boy Advance, or Pokemon Run & Bun. No license in this repository grants permission to distribute a commercial game ROM or third-party ROM hack.

## Pokémon Emerald map and trainer references

The trainer-event coordinates and map structure used by the interactive Emerald
atlas were derived from the community-maintained
[`pret/pokeemerald`](https://github.com/pret/pokeemerald) decompilation. The
project's render tools use a locally supplied, user-owned Emerald ROM to create
the map-floor and overworld trainer images under `web/static/emerald-maps/` and
`web/static/emerald-trainers/`.

Those rendered game graphics, Pokémon names, trainer characters, maps, and other
Pokémon audiovisual material remain property of their respective rights holders.
They are not relicensed by the root MIT License. No ROM data or save-state binary
is included in the judge-facing checkpoint library; only decoded battle metadata
needed by the simulator is distributed.
