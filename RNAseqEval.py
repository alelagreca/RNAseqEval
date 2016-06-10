#! /usr/bin/python

import sys, os
import re
import setup_RNAseqEval, paramsparser

# For copying SAM lines
import copy

from datetime import datetime

# To enable importing from samscripts submodulew
SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(SCRIPT_PATH, 'samscripts/src'))
import utility_sam
import Annotation_formats

from fastqparser import read_fastq
from report import EvalReport, ReportType

DISTANCE_THRESHOLD = 10000
MIN_OVERLAP_BASES = 5



# TODO: Osim broja readova koji pokrivaju pojedini gen, izracunati i coverage

# Parameter definitions for paramparser
paramdefs = {'-a' : 1,
             '--version' : 0,
             '-v' : 0,
             '-o' : 1,
             '--output' : 1}


def cleanup():
    pass


# A function that looks at exon maps and checks if an alignment is good and spliced
def isGoodSplitAlignment(exonhitmap, exoncompletemap, exonstartmap, exonendmap):

    isGood = True
    isSpliced = False
    if not (len(exonhitmap) == len(exoncompletemap) and len(exonhitmap) == len(exonstartmap) and len(exonhitmap) == len(exonendmap)):
        raise Exception('ERROR: Exon maps have unequal lengths (%d|%d|%d|%d)!' % (len(exonhitmap), len(exoncompletemap), len(exonstartmap), len(exonendmap)))

    for i in exonhitmap.keys():
        if exonhitmap[i] == 0:
            if exoncompletemap[i] <> 0:
                raise Exception('ERROR: HIT map 0 and COMPLETE map nonzero!')
            if exonstartmap[i] <> 0:
                raise Exception('ERROR: HIT map 0 and START map nonzero!')
            if exonendmap[i] <> 0:
                raise Exception('ERROR: HIT map 0 and END map nonzero!')

    # A list of indices of exons for which a hit map is nonzero
    hitlist = [i for i in exonhitmap.keys() if exonhitmap[i] > 0]

    if len(hitlist) == 0:
        return False, False

    starthit = hitlist[0]
    endhit = hitlist[-1]
    middlelist = hitlist[1:-1]

    # For an alignment to be spliced, the hit list has to skip some exons in the middle
    for x in hitlist[:-1]:
        if exonhitmap[x+1] - exonhitmap[x] > 1:
            isSpliced = True
            break           # No need to look further

    # For an alignment to be strictly good, it has to be uninterrupted
    # It has to end the first hit exon, start the last exon, end complete all in the middle
    middleOK = True
    for x in middlelist:
        if exoncompletmap[x] == 0:
            middleOK = False
            break           # No need to look further

    if (not middleOK) or exonstartmap[endhit] == 0 or exonendmap[starthit] == 0:
        isGood = False

    return isGood, isSpliced


# A helper function that extracts a chromosome name from a fasta header
# Chromosome names should be either chromosome [designation] or chr[designation]
# In the case header represents a mitochondrion, 'chrM' is returned!
def getChromName(header):
    chromname = ''
    # regular expressions for searching for long and short chromosome names
    longre = r'(chromosome )(\w*)'
    shortre = r'(chr)(\w*)'

    if header.find('mitochondrion') > -1 or header.find('chrM') > -1:
        chromname = 'chrM'
    else:
        match1 = re.search(longre, header)
        match2 = re.search(shortre, header)
        if match1:
            designation = match1.group(2)
            chromname = 'chr%s' % designation
        elif match2:
            designation = match2.group(2)
            chromname = 'chr%s' % designation
        else:
            chromname = 'genome'        # In case I can't detect chromosome designation or mitochondrion
                                        # I decide its a genome

    return chromname



def load_and_process_reference(ref_file, paramdict, report):
    # Reading FASTA reference
    [headers, seqs, quals] = read_fastq(ref_file)

    # Analyzing FASTA file

    # Since annotation file and SAM file (mapper output) do not have to have unified sequence names
    # for individual chromosomes, I'm creating a translation dictionary that will help me access the right
    # sequence more quickly. This will be based on the function which will attempt to analyze a string
    # and determine whether it refers to a genome, a particular chromosome or a mitochndia
    # The chromname2seq dictionary will have a inferred name as key, and index of the corresponding
    # sequence and headeer as value
    chromname2seq = {}

    if len(headers) == 1:
        report.reflength = len(seqs[0])
        chromname = getChromName(headers[0])
        report.chromlengths = {chromname : report.reflength}
        chromname2seq[chromname] = 0
    else:
        for i in xrange(len(headers)):
            header, seq = headers[i], seqs[i]
            chromname = getChromName(header)
            if chromname in report.chromlengths:
                raise Exception('\nERROR: Duplicate chromosome name: %s' % chromname)
                # sys.stderr.write('\nERROR: Duplicate chromosome name: %s' % chromname)
                exit()
            else:
                report.chromlengths[chromname] = len(seq[i])
                report.reflength += len(seq[i])
                chromname2seq[chromname] = i

    return [chromname2seq, headers, seqs, quals]



# checks if a given samline could form a split alignment with an existing samline list
# if so, adds the line to the list and returns True, otherwise returns False
def join_split_alignment(samline_list, samline):
    split_possible = True
    for sline in samline_list:
        if not utility_sam.possible_split_alignment(samline, sline, threshold = DISTANCE_THRESHOLD):
            split_possible = False
            break

    if split_possible:
        samline_list.append(samline)

    return split_possible



def load_and_process_SAM(sam_file, paramdict, report):
    # Loading SAM file into hash
    # Keeping only SAM lines with regular CIGAR string, and sorting them according to position
    qnames_with_multiple_alignments = {}
    [sam_hash, sam_hash_num_lines, sam_hash_num_unique_lines] = utility_sam.HashSAMWithFilter(sam_file, qnames_with_multiple_alignments)

    # Reorganizing SAM lines, removing unmapped queries, leaving only the first alignment and
    # other alignments that possibly costitute a split alignment together with the first one
    samlines = []
    for samline_list in sam_hash.itervalues():
        if samline_list[0].cigar <> '*':            # if the first alignment doesn't have a regular cigar string, skip
            pattern = '(\d+)(.)'
            operations = re.findall(pattern, samline_list[0].cigar)
            split = False

            for op in operations[1:-1]:             # Ns cannot appear as the first or the last operation
                if op[1] == 'N':
                    split = True
                    break
            # If the first alignment is split (had Ns in the middle), keep only the first alignment and drop the others
            if split:
                report.num_split_alignments += 1
                # Transform split alignments containing Ns into multiple alignments with clipping
                temp_samline_list = []
                posread = 0
                posref = 0
                newcigar = ''
                readlength = samline_list[0].CalcReadLengthFromCigar()
                new_samline = copy.deepcopy(samline_list[0])
                mapping_pos = new_samline.pos
                clipped_bases = new_samline.pos - new_samline.clipped_pos
                for op in operations:
                    if op[1] == 'N' and int(op[0]) > 1:        # Create a new alignment with clipping
                        newcigar += '%dH' % (readlength - posread)
                        new_samline.cigar = newcigar
                        # After some deliberation, I concluded that this samline doesn't have to have its position changed
                        # The next samline does, and by the size of N operation in cigar string
                        mapping_pos += int(op[0])
                        temp_samline_list.append(new_samline)
                        new_samline = copy.deepcopy(samline_list[0])
                        new_samline.pos = mapping_pos
                        new_samline.clipped_pos = new_samline.pos - clipped_bases
                        posref += int(op[0])
                        newcigar = '%dH' % posread
                    else:                   # Expand a current alignment
                        newcigar += op[0] + op[1]
                        if op[1] in ('D', 'N'):
                            posref += int(op[0])
                        elif op[1] == 'I':
                            posread += int(op[0])
                            # Everythin besided deletes and Ns will be clipped in the next partial alignment
                            # Therefore have to adjust both pos and clipped pos
                            clipped_bases += int(op[0])
                            mapping_pos += int(op[0])
                        else:
                            posref += int(op[0])
                            posread += int(op[0])
                            clipped_bases += int(op[0])
                            mapping_pos += int(op[0])

                new_samline.cigar = newcigar
                temp_samline_list.append(new_samline)

                samlines.append(temp_samline_list)
            else:
                temp_samline_list = [samline_list[0]]        # add the first alignment to the temp list
                multi_alignment = False
                for samline in samline_list[1:]:            # look through other alignments and see if they could form a split alignment with the current temp_samline_list
                    if not join_split_alignment(temp_samline_list, samline):
                        multi_alignment = True

                if multi_alignment:
                    report.num_multi_alignments += 1
                if len(temp_samline_list) > 1:
                    report.num_possibly_split_alignements += 1
                samlines.append(temp_samline_list)
        else:
            pass

    # Sorting SAM lines according to the position of the first alignment
    samlines.sort(key = lambda samline: samline[0].pos)

    # Calculate real split alignments
    num_real_split = 0
    for samline_list in samlines:
        if len(samline_list) > 1:
            num_real_split += 1

    report.num_alignments = sam_hash_num_lines
    report.num_unique_alignments = sam_hash_num_unique_lines
    report.num_real_alignments = len(samlines)
    report.num_real_split_alignments = num_real_split
    report.num_non_alignments = report.num_alignments - len(samlines)       # Not sure if this is correct any more

    return samlines



def load_and_process_annotations(annotations_file, paramdict, report):
    # Reading annotation file
    annotations = Annotation_formats.Load_Annotation_From_File(annotations_file)

    # Sorting annotations according to position
    # NOTE: Might not be necessary because they are generally already sorted in a file
    annotations.sort(reverse=False, key=lambda annotation: annotation.start)

    # Analyzing annotations

    # Looking at expressed genes, ones that overlap with at least one read in SAM file
    # Storing them in a dictionary together with a number of hits for each exon in the gene
    expressed_genes = {}

    # Calculating gene coverage, how many bases of each gene and exon are covered by ready
    # Bases covered multiple times are taken into account multiple times
    # The structure of this dictionary is similar to expressed_genes above
    # Each gene has one global counter (index 0), and one counter for each exon
    gene_coverage = {}

    report.totalGeneLength = 0
    report.num_genes = len(annotations)
    report.max_exons_per_gene = 1       # Can not be less than 1
    report.num_exons = 0
    sumGeneLength = 0.0
    sumExonLength = 0.0
    for annotation in annotations:
        # Initializing a list of counters for a gene
        # Each gene has one global counted (index 0), and one counter for each exon
        expressed_genes[annotation.genename] = [0 for i in xrange(len(annotation.items) + 1)]
        gene_coverage[annotation.genename] = [0 for i in xrange(len(annotation.items) + 1)]

        if len(annotation.items) > 1:
            report.num_multiexon_genes += 1

        # Determining a maximum number of exons per gene
        if len(annotation.items) > report.max_exons_per_gene:
            report.max_exons_per_gene = len(annotation.items)

        report.totalGeneLength += annotation.getLength()
        chromname = getChromName(annotation.seqname)
        if chromname in report.chromlengths:
            report.chromlengths[chromname] += annotation.getLength()
        else:
            report.chromlengths[chromname] = annotation.getLength()
        report.num_exons += len(annotation.items)
        glength = annotation.getLength()
        if glength < report.min_gene_length or report.min_gene_length == 0:
            report.min_gene_length = glength
        if glength > report.max_gene_length or report.max_gene_length == 0:
            report.max_gene_length = glength
        sumGeneLength += glength
        for item in annotation.items:
            elength = item.getLength()
            if elength < report.min_exon_length or report.min_exon_length == 0:
                report.min_exon_length = elength
            if elength > report.max_exon_length or report.max_exon_length == 0:
                report.max_exon_length = elength
            sumExonLength += elength
    report.avg_gene_length = sumGeneLength / report.num_genes
    report.avg_exon_length = sumExonLength / report.num_exons

    return annotations, expressed_genes, gene_coverage



# TODO: Refactor code, place some code in functions
#       Rewrite analyzing SAM file, detecting multi and split alignments
def eval_mapping_annotations(ref_file, sam_file, annotations_file, paramdict):

    sys.stderr.write('\n')
    sys.stderr.write('\n(%s) START: Evaluating mapping with annotations:' % datetime.now().time().isoformat())

    report = EvalReport(ReportType.ANNOTATION_REPORT)

    sys.stderr.write('\n(%s) Loading and processing FASTA reference ... ' % datetime.now().time().isoformat())
    [chromname2seq, headers, seqs, quals] = load_and_process_reference(ref_file, paramdict, report)

    sys.stderr.write('\n(%s) Loading and processing SAM file with mappings ... ' % datetime.now().time().isoformat())
    samlines = load_and_process_SAM(sam_file, paramdict, report)

    sys.stderr.write('\n(%s) Loading and processing annotations file ... ' % datetime.now().time().isoformat())
    annotations, expressed_genes, gene_coverage = load_and_process_annotations(annotations_file, paramdict, report)

    numq = 0
    sumq = 0.0

    sys.stderr.write('\n(%s) Analyzing mappings against annotations ... ' % datetime.now().time().isoformat())
    # Looking at SAM lines to estimate general mapping quality
    # TODO: This used to take a long time, but I managed to speed it up
    #       should be looked at a bit more to see if additional improvements could be made.

    sys.stderr.write('\n(%s) Calculating chosen quality statistics ... ' % datetime.now().time().isoformat())
    # Calculating chosen quality statistics
    # Seprataing it from other analysis for clearer code
    for samline_list in samlines:
        for samline in samline_list:
            quality = samline.chosen_quality
            if quality > 0:
                report.num_good_quality += 1
                if report.max_mapping_quality == 0 or report.max_mapping_quality < quality:
                    report.max_mapping_quality = quality
                if report.min_mapping_quality == 0 or report.min_mapping_quality > quality:
                    report.min_mapping_quality = quality
                numq += 1
                sumq += quality
            else:
                report.num_zero_quality += 1


    # Calculating general mapping statistics
    # Match/Mismatch/Insert/Delete
    # TODO: Percentage of reads mapped
    numMatch = 0
    numMisMatch = 0
    numInsert = 0
    numDelete = 0

    total_read_length = 0
    total_bases_aligned = 0
    percentage_bases_aligned = 0.0


    # Setting up some sort of a progress bar
    sys.stderr.write('\n(%s) Analyzing CIGAR strings ...  ' % datetime.now().time().isoformat())
    sys.stderr.write('\nProgress: | 1 2 3 4 5 6 7 8 9 0 |')
    sys.stderr.write('\nProgress: | ')
    numsamlines = len(samlines)
    progress = 0
    currentbar = 0.1
    for samline_list in samlines:
        # Callculating progress
        progress += 1
        if float(progress)/numsamlines >= currentbar:
            sys.stderr.write('* ')
            currentbar += 0.1
        # Calculate readlength from the first alignment (should be the same)
        # and then see how many of those bases were actually aligned
        readlength = samline_list[0].CalcReadLengthFromCigar()
        basesaligned = 0
        for samline in samline_list:
            chromname = getChromName(samline.rname)
            if chromname not in chromname2seq:
                raise Exception('\nERROR: Unknown chromosome name in SAM file! (chromname:"%s", samline.rname:"%s")' % (chromname, samline.rname))
            chromidx = chromname2seq[chromname]

            cigar = samline.CalcExtendedCIGAR(seqs[chromidx])
            pos = samline.pos
            quals = samline.qual

            # Using regular expressions to find repeating digit and skipping one character after that
            # Used to separate CIGAR string into individual operations
            pattern = '(\d+)(.)'
            operations = re.findall(pattern, cigar)

            for op in operations:
                if op[1] in ('M', '='):
                    numMatch += int(op[0])
                    basesaligned += int(op[0])
                elif op[1] == 'I':
                    numInsert += int(op[0])
                    basesaligned += int(op[0])
                elif op[1] == 'D':
                    numDelete += int(op[0])
                elif op[1] =='X':
                    numMisMatch += int(op[0])
                    basesaligned += int(op[0])
                elif op[1] in ('N', 'S', 'H', 'P'):
                    pass
                else:
                    sys.stderr.write('\nERROR: Invalid CIGAR string operation (%s)' % op[1])

        total_read_length += readlength
        total_bases_aligned += basesaligned
        if basesaligned > readlength:
            import pdb
            pdb.set_trace()
            # raise Exception('\nERROR counting aligned and total bases!')
            # TODO: See what happens here
            pass

    # Closing progress bar
    sys.stderr.write('|')
    sys.stderr.write('\nDone!')

    percentage_bases_aligned = float(total_bases_aligned) / total_read_length

    report.sum_read_length = total_read_length
    report.sum_bases_aligned = total_bases_aligned
    report.percentage_bases_aligned = percentage_bases_aligned

    # Calculating gene/exon hit precission statistics

    # Number of alignments covering multiple genes/exons
    multi_exon_hits = 0
    multi_gene_hits = 0

    # Setting up some sort of a progress bar
    sys.stderr.write('\n(%s) Analyzing mappings ...  ' % datetime.now().time().isoformat())
    sys.stderr.write('\nAnalyzing mappings ... ')
    sys.stderr.write('\nProgress: | 1 2 3 4 5 6 7 8 9 0 |')
    sys.stderr.write('\nProgress: | ')
    numsamlines = len(samlines)
    progress = 0
    currentbar = 0.1

    # Each samline list in samlines represents a single alignment
    # If a samline list contains multiple samlines, they all represent a single split alignment
    for samline_list in samlines:
        # Calculating progress
        progress += 1
        if float(progress)/numsamlines >= currentbar:
            sys.stderr.write('* ')
            currentbar += 0.1

        # Initializing information for a single read
        genescovered = []   # genes covered by an alignment
        badsplit = False
        hit = False
        exonHit = False
        exon_cnt = 0        # counting exons spanned by an alignement
        gene_cnt = 0        # counting genes spanned by an alignement
        num_alignments = len(samline_list)
        if num_alignments > 1:
            split = True
        else:
            split = False

        # Assuming that all parts of the split alignment are on the same chromosome
        chromname = getChromName(samline_list[0].rname)
        if chromname not in chromname2seq:
            raise Exception('\nERROR: Unknown chromosome name in SAM file! (chromname:"%s", samline.rname:"%s")' % (chromname, samline.rname))
        chromidx = chromname2seq[chromname]

        # TODO: Separate code for split and contiguous alignments
        #       Might make the code easier to read

        # PLAN:
        # - calculate reference length for a split read
        # - check for genes that it intersects
        # - then iterate over parts of alignment and exons to evaluate how well the alignment captures the transcript

        # Calculating total alignment reference length for all parts of a  split read
        # A distance between the start of the first alignment and the end of the last alignment
        # If all split alignments of the read were sorted according to position, this could be done faster
        readrefstart = -1
        readrefend = -1
        for samline in samline_list:
            # start = samline.pos
            start = samline.pos
            reflength = samline.CalcReferenceLengthFromCigar()
            end = start + reflength

            if readrefstart == -1 or readrefstart < start:
                readrefstart = start
            if readrefend == -1 or readrefend > end:
                readrefend = end

        readreflength = readrefend - readrefstart
        startpos = readrefstart
        endpos = readrefend

        # Assuming all samlines in samline_list have the same strand
        if samline_list[0].flag & 16 == 0:
            readstrand = Annotation_formats.GFF_STRANDFW
        else:
            readstrand = Annotation_formats.GFF_STRANDRV

        for annotation in annotations:
            # If its the same chromosome, the same strand and read and gene overlap, then proceed with analysis
            if chromname == getChromName(annotation.seqname) and readstrand == annotation.strand and annotation.overlapsGene(startpos, endpos):
                if annotation.genename not in genescovered:
                    genescovered.append(annotation.genename)
                    gene_cnt += 1
                hit = True

                if num_alignments > len(annotation.items):
                    # TODO: BAD split!! Alignment is split, but annotation is not!
                    badsplit = True
                    sys.stderr.write('\nWARNING: Bad split alignment with more parts then annotation has exons!\n')

                # Updating gene expression
                # Since all inital values for expression and coverage are zero, this could all probably default to case one
                if annotation.genename in expressed_genes.keys():
                    expressed_genes[annotation.genename][0] += 1
                    gene_coverage[annotation.genename][0] += annotation.basesInsideGene(startpos, endpos)
                else:
                    expressed_genes[annotation.genename][0] = 1
                    gene_coverage[annotation.genename][0] = annotation.basesInsideGene(startpos, endpos)

                if annotation.insideGene(startpos, endpos):
                    partial = False
                else:
                    partial = True

                # Initialize exon hit map and exon complete map (also start and end map)
                # Both have one entery for each exon
                # Hit map collects how many times has each exon been hit by an alignment (it should be one or zero)
                # Complete map collects which exons have been completely covered by an alignement
                # Start map collects which exons are correctly started by an alignment (have the same starting position)
                # End map collects which exons are correctly ended by an alignment (have the same ending position)
                # NOTE: test this to see if it slows the program too much
                exonhitmap = {(i+1):0 for i in xrange(len(annotation.items))}
                exoncompletemap = {(i+1):0 for i in xrange(len(annotation.items))}
                exonstartmap = {(i+1):0 for i in xrange(len(annotation.items))}
                exonendmap = {(i+1):0 for i in xrange(len(annotation.items))}
                for samline in samline_list:
                    item_idx = 0
                    for item in annotation.items:
                        item_idx += 1
                        if item.overlapsItem(startpos, endpos):
                            exonhitmap[item_idx] += 1
                            if item.equalsItem(startpos, endpos):
                                exoncompletemap[item_idx] = 1
                                exonstartmap[item_idx] = 1
                                exonendmap[item_idx] = 1
                            elif item.startsItem(startpos, endpos):
                                exonstartmap[item_idx] = 1
                            elif item.endsItem(startpos, endpos):
                                exonendmap[item_idx] = 1

                            exon_cnt += 1
                            expressed_genes[annotation.genename][item_idx] += 1
                            gene_coverage[annotation.genename][item_idx] += item.basesInside(startpos, endpos)
                            exonHit = True
                            if item.insideItem(startpos, endpos):
                                exonPartial = False
                            else:
                                exonPartial = True

                    # TODO: What to do if an exon is partially hit?
                    # NOTE: Due to information in hip map and complete map
                    #       This information might be unnecessary
                    #       It can be deduced from exon maps

                # Analyzing exon maps to extract some statistics
                num_exons = len(annotation.items)
                num_covered_exons = len([x for x in exonhitmap.values() if x > 0])       # Exons are considered covered if they are in the hit map
                                                                                # This means that they only have to be overlapping with an alignment!

                if num_covered_exons > 0:
                    report.num_cover_some_exons += 1    # For alignments covering multiple genes, this will be calculated more than once

                if num_covered_exons == num_exons:
                    report.num_cover_all_exons += 1

                num_equal_exons = len([x for x in exoncompletemap.values() if x > 0])
                report.num_equal_exons += num_equal_exons
                report.num_partial_exons += num_covered_exons - num_equal_exons

                # Exons covered by more than one part of a split alignment
                multicover_exons = len([x for x in exonhitmap.values() if x > 1])
                report.num_multicover_exons += multicover_exons

                # Not sure what to do with this
                report.num_undercover_alignments = 0
                report.num_overcover_alignments = 0

                # Exon start and edn position
                num_good_starts = len([x for x in exonstartmap.values() if x > 0])
                num_good_ends = len([x for x in exonendmap.values() if x > 0])
                report.num_good_starts += num_good_starts
                report.num_good_ends += num_good_ends

                isGood, isSpliced = isGoodSplitAlignment(exonhitmap, exoncompletemap, exonstartmap, exonendmap)

                if isSpliced:
                    report.num_possible_spliced_alignment += 1

                if isGood:
                    report.num_good_alignment += 1
                else:
                    report.num_bad_alignment += 1


        if exon_cnt > 1:
            report.num_multi_exon_alignments += 1
        elif exon_cnt == 0:
            report.num_cover_no_exons += 1

        if len(genescovered) > 1:
            report.num_multi_gene_alignments += 1

        if badsplit:
            report.num_bad_split_alignments += 1

        if hit and not partial:
            report.num_hit_alignments += 1
        elif hit and partial:
            report.num_partial_alignments += 1
        else:
            report.num_missed_alignments += 1

        if exonHit and not exonPartial:
            report.num_exon_hit += 1
        elif exonHit and exonPartial:
            report.num_exon_partial += 1
        else:
            report.num_exon_miss += 1

        if hit and not exonHit:
            report.num_inside_miss_alignments += 1

        if len(genescovered) == 1 and not badsplit:
            report.num_good_alignment += 1
        else:
            report.num_bad_alignment += 1

    # Closing progress bar
    sys.stderr.write('|')
    sys.stderr.write('\nDone!')

    report.good_alignment_percent = 100.0 * float(report.num_good_alignment)/(report.num_good_alignment + report.num_bad_alignment)
    report.bad_alignment_percent = 100.0 * float(report.num_bad_alignment)/(report.num_good_alignment + report.num_bad_alignment)

    # How many genes were covered by alignments
    report.num_genes_covered = 0
    report.num_exons_covered = 0
    for genecnt in expressed_genes.itervalues():
        if genecnt[0] > 0:
            report.num_genes_covered += 1
        for cnt in genecnt[1:]:
            if cnt > 0:
                report.num_exons_covered += 1

    # TODO: calculate coverage of partial alignments
    #       work with split alignments (ignore Ns)
    #       expand the same logic to exons instead of complete genes

    report.num_match = numMatch
    report.num_mismatch = numMisMatch
    report.num_insert = numInsert
    report.num_delete = numDelete

    total = numMatch + numMisMatch + numInsert + numDelete

    if total > 0:
        report.match_percentage = float(report.num_match)/total
        report.mismatch_percentage = float(report.num_mismatch)/total
        report.insert_percentage = float(report.num_insert)/total
        report.delete_percentage = float(report.num_delete)/total

    if numq > 0:
        report.avg_mapping_quality = sumq / numq

    # Pass gene expression and coverage information to report
    report.expressed_genes = expressed_genes
    report.gene_coverage = gene_coverage

    sys.stderr.write('\n(%s) Done!' % datetime.now().time().isoformat())
    sys.stderr.write('\n')

    return report



def eval_mapping_fasta(ref_file, sam_file, paramdict):

    sys.stderr.write('\n')
    sys.stderr.write('\n(%s) START: Evaluating mapping with FASTA reference only:' % datetime.now().time().isoformat())

    report = EvalReport(ReportType.FASTA_REPORT)

    sys.stderr.write('\n(%s) Loading and processing FASTA reference ... ' % datetime.now().time().isoformat())
    [chromname2seq, headers, seqs, quals] = load_and_process_reference(ref_file, paramdict, report)

    sys.stderr.write('\n(%s) Loading and processing SAM file with mappings ... ' % datetime.now().time().isoformat())
    samlines = load_and_process_SAM(sam_file, paramdict, report)

    numq = 0
    sumq = 0.0

    # Analyzing mappings
    sys.stderr.write('\n(%s) Analyzing mappings against FASTA reference ... ' % datetime.now().time().isoformat())

    numMatch = 0
    numMisMatch = 0
    numInsert = 0
    numDelete = 0

    # Looking at SAM lines to estimate general mapping quality
    for samline_list in samlines:
        for samline in samline_list:
            quality = samline.chosen_quality
            if quality > 0:
                report.num_good_quality += 1
                if report.max_mapping_quality == 0 or report.max_mapping_quality < quality:
                    report.max_mapping_quality = quality
                if report.min_mapping_quality == 0 or report.min_mapping_quality > quality:
                    report.min_mapping_quality = quality
                numq += 1
                sumq += quality
            else:
                report.num_zero_quality += 1

            chromname = getChromName(samline.rname)
            if chromname not in chromname2seq:
                raise Exception('\nERROR: Unknown choromosome name in SAM file! (chromname:"%s", samline.rname:"%s")' % (chromname, samline.rname))
            chromidx = chromname2seq[chromname]

            cigar = samline.CalcExtendedCIGAR(seqs[chromidx])
            pos = samline.pos
            quals = samline.qual

            # Using regular expressions to find repeating digit and skipping one character after that
            pattern = '(\d+)(.)'
            operations = re.findall(pattern, cigar)

            for op in operations:
                if op[1] in ('M', '='):
                    numMatch += int(op[0])
                elif op[1] == 'I':
                    numInsert += int(op[0])
                elif op[1] == 'D':
                    numDelete += int(op[0])
                elif op[1] =='X':
                    numMisMatch += int(op[0])
                elif op[1] in ('N', 'S', 'H', 'P'):
                    pass
                else:
                    sys.stderr.write('\nERROR: Invalid CIGAR string operation (%s)' % op[1])

    report.num_match = numMatch
    report.num_mismatch = numMisMatch
    report.num_insert = numInsert
    report.num_delete = numDelete

    total = numMatch + numMisMatch + numInsert + numDelete

    if total > 0:
        report.match_percentage = float(report.num_match)/total
        report.mismatch_percentage = float(report.num_mismatch)/total
        report.insert_percentage = float(report.num_insert)/total
        report.delete_percentage = float(report.num_delete)/total

    if numq > 0:
        report.avg_mapping_quality = sumq / numq

    sys.stderr.write('\n(%s) Done!' % datetime.now().time().isoformat())
    sys.stderr.write('\n')

    return report


def eval_mapping(ref_file, sam_file, paramdict):

    out_filename = ''
    out_file = None

    if '-o' in paramdict:
        out_filename = paramdict['-o'][0]
    elif '--output' in paramdict:
        out_filename = paramdict['--output'][0]

    if out_filename != '':
        out_file = open(out_filename, 'w+')
    else:
        out_file = sys.stdout


    if '-a' in paramdict:
        annotations_file = paramdict['-a'][0]
        report = eval_mapping_annotations(ref_file, sam_file, annotations_file, paramdict)
    else:
        report = eval_mapping_fasta(ref_file, sam_file, paramdict)

    report.commandline = paramdict['command']

    out_file.write(report.toString())


def verbose_usage_and_exit():
    sys.stderr.write('RNAseqEval - A tool for evaulating RNAseq results.\n')
    sys.stderr.write('\n')
    sys.stderr.write('Usage:\n')
    sys.stderr.write('\t%s [mode]\n' % sys.argv[0])
    sys.stderr.write('\n')
    sys.stderr.write('\tmode:\n')
    sys.stderr.write('\t\tsetup\n')
    sys.stderr.write('\t\tcleanup\n')
    sys.stderr.write('\t\teval-mapping\n')
    sys.stderr.write('\n')
    exit(0)

if __name__ == '__main__':
    if (len(sys.argv) < 2):
        verbose_usage_and_exit()

    mode = sys.argv[1]

    if (mode == 'setup'):
        if (len(sys.argv) != 2):
            sys.stderr.write('Setup the folder structures and install necessary tools.\n')
            sys.stderr.write('Requires no additional parameters to run.\n')
            sys.stderr.write('\n')
            exit(1)

        setup_RNAseqEval.setup_all()

    elif (mode == 'cleanup'):
        if (len(sys.argv) != 2):
            sys.stderr.write('Cleans up intermediate files.\n')
            sys.stderr.write('Requires no additional parameters to run.\n')
            sys.stderr.write('\n')
            exit(1)

        cleanup()

    elif (mode == 'eval-mapping'):
        if (len(sys.argv) < 4):
            sys.stderr.write('Evaluates RNAseq mapping from a SAM file.\n')
            sys.stderr.write('Can use annotations if provided.\n')
            sys.stderr.write('Usage:\n')
            sys.stderr.write('%s %s <reference FASTA file> <input SAM file> options\n'% (sys.argv[0], sys.argv[1]))
            sys.stderr.write('options:"\n')
            sys.stderr.write('-a <file> : a reference annotation (GFF/GTF/BED) file\n')
            sys.stderr.write('-o (--output) <file> : output file to which the report will be written\n')
            sys.stderr.write('\n')
            exit(1)

        ref_file = sys.argv[2]
        sam_file = sys.argv[3]

        pparser = paramsparser.Parser(paramdefs)
        paramdict = pparser.parseCmdArgs(sys.argv[4:])

        ref_file = sys.argv[2]
        sam_file = sys.argv[3]

        paramdict['command'] = ' '.join(sys.argv)

        eval_mapping(ref_file, sam_file, paramdict)

    else:
        print 'Invalid mode!'
