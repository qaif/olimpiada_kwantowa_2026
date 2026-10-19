#!/usr/bin/env perl
# webTEX biber guard. Unlike TeX, biber ignores openin_any, so a document could make it read any
# file the compile process can read (\addbibresource{/abs/path}, ../, globs). This wrapper runs
# biber only if every data source in the .bcf is a relative path inside the project, and with
# --noconf so a biber.conf uploaded into the project cannot change input directories.
# Usage (set by server/compiler.js): $biber = 'perl biber-guard.pl %O %S'
use strict;
use warnings;

my $base = @ARGV ? $ARGV[-1] : '';
my $bcf = $base =~ /\.bcf$/ ? $base : "$base.bcf";
open(my $fh, '<:raw', $bcf) or die "webTEX: cannot read $bcf\n";
my $xml = do { local $/; <$fh> };
close $fh;

my @bad;
while ($xml =~ m{<bcf:datasource\b([^>]*)>(.*?)</bcf:datasource>}gs) {
  my ($attrs, $src) = ($1, $2);
  for ($src) {
    s/&lt;/</g; s/&gt;/>/g; s/&quot;/"/g; s/&apos;/'/g;
    s/&#(\d+);/chr($1)/ge; s/&#x([0-9a-fA-F]+);/chr(hex $1)/ge; s/&amp;/&/g;
  }
  push @bad, $src
    if $src =~ m{^\s*[/\\~]}               # absolute or home-relative
    || $src =~ m{^\s*[A-Za-z]:}             # drive letter
    || $src =~ m{\.\.}                      # parent directory
    || $src =~ m/[{}\x00]/                  # glob brace expansion can produce any of the above
    || $src =~ m{^\s*[A-Za-z][\w+.-]*://}   # remote data source
    || $attrs =~ m{\btype="(?!file")};
}
push @bad, 'input_directory/output_directory option'
  if $xml =~ m{<bcf:key>\s*(?:input_directory|output_directory)\s*</bcf:key>};

if (@bad) {
  print STDERR "webTEX: biber refused bibliography data source(s) outside the project: ", join(', ', @bad), "\n";
  print STDERR "webTEX: use \\addbibresource{file.bib} with a file uploaded to the project.\n";
  exit 2;
}
exec('biber', '--noconf', @ARGV) or die "webTEX: cannot run biber: $!\n";
