# RNAseqEval.py
Run RNAseqEval.py for general evaulation of mappings in sam file against reference and optionally annotations. This script is intended to evaluate real dataset mapping. Run RNAseqEval.py without any arguments to print options.

Usage:
     
    RNAseqEval.py eval-mapping <reference FASTA file> <input SAM file> options

## Usage modes
RNAseqEval.py script can be used in three differents modes, determined by the first argument. Each mode requires different parameters and allowes different options.

### eval-mapping
Used in eval-mapping mode, RNAseqEval.py script is used to evaluate RNAseq mappings against known FASTA reference and annotations. Annotations can be omitted, but in that case the script will provide only basic output.

Usage:

    RNAseqEval.py eval-mapping <reference FASTA file> <input SAM file> options
    
Allowed options:

    -a <file> : a reference annotation (GFF/GTF/BED) file
    -o (--output) <file> : output file to which the report will be written

### eval-annotations
Used in eval-annotations mode, RNAseqEval.py script will print out basic information on a annotations file.

Usage:

    RNAseqEval.py eval-annotations <annotations file> options

Allowed options:

    -o (--output) <file> : output file to which the report will be written

### eval-maplength
Used in eval-maplength mode, RNAseqEval script will return mapped percentage for each read

Usage:

    RNAseqEval.py eval-maplength <input SAM file> options

Options:

    -o (--output) <file> : output file to which the report will be written

Oposed to first two modes which calculate certain statistical information from input files, in eval-maplength mode the script will print out information on each read in CSV format (on the screen or in a file). The folowinf information is printed out:
- readname name (header "QNAME")
- reference name (header "RNAME")
- read length (header "read length")
- the number of bases aligned for that read (header "bases aligned")

## Output for eval-mapping and eval-annotations modes
Depending on the usage mode, RNAseqEval.py script will display various information about input files and the results of the analysis.

General information on FASTA reference and mapping SAM file:

    - Reference length - In eval-mapping mode this will be the total lenght of all chromosomes in a FASTA rederence, while in eval-annotations mode this will be the total length of all genes.
    - Number of chromosomes
    - List of chromosomes
    - Number of alignments in SAM file (total / unique)
    - Alignments with / without CIGAR string
    - Mapping quality without zeroes (avg / min / max)
    - Alignments with mapping quality (>0 / =0)
    - Number of matches / mismatches / inserts / deletes
    - Percentage of matches / mismatches / inserts / deletes

Annotation statistics:

    - Total gene length
    - Total number of transcripts
    - Total number of exons
    - Number of multiexon transcripts
    - Maximum number of exons in a gene
    - Gene size (Min / Max / Avg)
    - Exon size (Min / Max / Avg)

Annotations that are on the same chromosome and strand and that overlap each other are grouped into annotation groups. Each group should represent a gene, while each annotation in a group should represent one possible splicing for that gene.

    - Number of annotation groups (genes)
    - Number of genes with alternate splicing
    - Maximum / minimum number of alternate spliced alignments for a gene
    - Maximum / minimum number of exons in spliced alignments

Mapping quality information obtained by comparing alignements in a SAM file to given annotations. Only in eval-mapping mode if annotations are provided.

     - Total number and percentage of bases aligned for all reads
     - The number of transcripts (annotations) "hit" by all reads
     - Total number of exons "hit" by all reads
     - Number of alignments with "hit" on transcripts
     - Number of alignments with "hit" on exons
     - Number of alignments matching a beginning and an end of an exon
     - Number of contiguous and non contiguous alignments

The script also calculates gene expression and gene/exon coverage information. this information is printed only in eval-mapping mode if annotations are provided. The script will output the number of expressed transcripts. A transcript is considered expressed if at least one read is mapped to its position. For each transcript, the script also prints out the following :
- transcript name
- number of exons
- number of reads that align to it
- total number of bases aligned to it
- for each exon in the transcript
     - number of reads aligned to it
     - total number of bases aligned to it