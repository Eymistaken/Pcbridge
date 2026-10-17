# Bundled data

## eff_large_wordlist.txt

The EFF Large Wordlist for Passphrases, by the Electronic Frontier Foundation,
used unchanged under the Creative Commons Attribution 3.0 United States
license (CC BY 3.0 US, https://creativecommons.org/licenses/by/3.0/us/).

- Source: https://www.eff.org/files/2016/07/18/eff_large_wordlist.txt
- About it: https://www.eff.org/deeplinks/2016/07/new-wordlists-random-passphrases
- sha256: addd35536511597a02fa0a9ff1e5284677b8883b83e986e43f15a3db996b903e
- 7776 words; `pcbridge/passphrase.py` leaves out the four hyphenated ones,
  since its words are joined with hyphens.

pcbridge uses it to suggest the remote password: six words chosen with the
`secrets` module, about 77 bits.
