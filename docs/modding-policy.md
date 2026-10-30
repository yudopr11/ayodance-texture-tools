# Game modification policy and license scope

This is an unofficial project, not endorsed by Megaxus or the game's other rights
holders. A license for these tools is not permission to modify or redistribute
the game. This summary is not legal advice.

## Official AyoDance rules

Sources reviewed on **2026-10-30**:

- [Official AyoDance rules](https://ayodance.megaxus.com/rules), player rule **6**:
  "Dilarang memodifikasi game, situs, atau bagian mana pun di dalamnya, termasuk
  mentransmisikan kode, file, virus atau spyware yang mengganggu fungsi game."
  In English: modifying the game, website or any part of them is prohibited,
  including transmitting code/files/viruses/spyware that interfere with the game.
  The listed sanction is **Permanent block**. The rule does not state an exception
  for cosmetic texture replacements or renderer shims.
- The same page's player rule **5** prohibits illegal programs, third-party
  software or cheats for advantage in the game or web events, including sharing
  them; it also lists **Permanent block**. This is separate from rule 6: a claim
  of no gameplay advantage does not establish permission to modify the game.
- [Audition AyoDance EULA](https://member.megaxus.com/home/au_eula.htm), service
  clauses **3 and 5**, assert ownership of game elements and restrict creating,
  distributing or selling products covered by Megaxus's copyright/patent rights
  without official permission. Clause **8** requires use of the client provided
  through Megaxus's distribution channels. Clauses **6 and 12** provide for
  sanctions/account blocking and possible legal action; clause **9** permits
  changes to the terms without prior notice.

The member [registration page](https://member.megaxus.com/?op=register) also
presents an EULA, but its general section names Counter-Strike Online. The
AyoDance-specific rules and EULA above are the primary sources for this summary.
The pages do not provide a clear effective date; check current rules and any
installer/in-game agreement rather than assuming this summary is exhaustive.

**Do not assume these tools are permitted on the live service.** Obtain written
permission from Megaxus covering the proposed archive changes, renderer DLL and
any distribution before deployment. An offline/private copy, a backup, or a
successful technical test does not by itself grant permission. No protection or
anti-cheat bypass is provided or recommended.

## Repository license versus third-party rights

The repository's author-owned code and documentation are offered under the
[MIT License](../LICENSE). MIT is a standard, permissive source-code license: it
allows reuse, modification and distribution, including commercial reuse, while
requiring retention of its copyright and permission notices and disclaiming
warranty. It does not certify compliance with a game's rules.

That grant applies only to rights the contributors can grant. It does **not**
license game archives, textures, music, client binaries, trademarks, or any other
third-party material. Reverse-engineering notes or recovered material do not
transfer the original rights holders' rights. Dependencies, external tools and
models retain their own licenses and notices; they are not relicensed by this
repository.

In particular, UltraSharpV2 is separately published under **CC-BY-NC-SA-4.0**;
see the [author's model repository](https://huggingface.co/Kim2091/UltraSharpV2)
and its actual license terms. MIT for the tooling does not remove those terms,
authorize distributing game-derived textures, or establish what rights exist in
an enhanced output. Keep game assets and downloaded models/tools out of source
releases. Seek the relevant rights holders' permission or qualified legal advice
before distributing a texture pack or modified client.
