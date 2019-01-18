"""
This module contains classes for storing atomic data.

The Frame class may contain multiple Residues which may each contain multiple Atoms.
Both Frame and Residue are iterable. Residue is indexable with either atom numbers or names.
"""

import logging

import numpy as np

from .util import backup_file, file_write_lines
from .parsers.cfg import CFG

logger = logging.getLogger(__name__)

np.seterr(all="raise")


# Create FileNotFoundError if using older version of Python
try:
    try:
        raise FileNotFoundError
    except FileNotFoundError:
        pass
except NameError:
    class FileNotFoundError(OSError):
        pass


class Atom:
    """
    Hold data for a single atom
    """
    __slots__ = ["name", "num", "type", "mass", "charge", "coords"]

    def __init__(self, name, num, type=None, mass=None, charge=None, coords=None):
        """
        Create an atom.

        :param str name: The name of the atom
        :param int num: The atom number
        :param str type: The atom type
        :param float mass: The mass of the atom
        :param float charge: The charge of the atom
        :param coords: The coordinates of the atom
        """
        self.name = name
        self.num = num
        self.type = type
        self.mass = mass
        self.charge = charge
        self.coords = coords

    def __repr__(self):
        return "Atom #{0} {1} type: {2} mass: {3} charge: {4}".format(
            self.num, self.name, self.type, self.mass, self.charge
        )

    def add_missing_data(self, other):
        assert self.name == other.name
        assert self.num == other.num

        for attr in ("type", "mass", "charge", "coords"):
            if getattr(self, attr) is None:
                setattr(self, attr, getattr(other, attr))


class Molecule:
    """
    Holds data for a molecule comprised of multiple residues
    """
    __slots__ = ["resnames", "bonds", "beads", "resid_to_beads", "resid_to_resname"]

    def __init__(self, resnames, bonds, beads):
        self.resnames = resnames
        self.resid_to_resname = dict(zip(range(1, len(resnames)+1), resnames))
        self.resid_to_beads = dict(zip(range(1, len(resnames)+1), beads))
        self.bonds = bonds
        self.beads = np.array(beads).flatten().tolist()


class Residue:
    """
    Hold data for a residue - list of atoms
    """
    __slots__ = ["name", "num", "atoms", "name_to_num"]

    def __init__(self, name=None, num=None):
        self.atoms = []
        self.name = name
        self.num = num
        self.name_to_num = {}

    def __iter__(self):
        return iter(self.atoms)

    def __getitem__(self, item):
        try:
            return self.atoms[self.name_to_num[item]]
        except KeyError:
            pass

        try:
            return self.atoms[item]
        except TypeError as e:
            e.args = ("Atom {0} does not exist in residue {1}".format(item, self.name),)
            raise

    def __len__(self):
        return len(self.atoms)

    def __contains__(self, item):
        return item in self.name_to_num

    def add_atom(self, atom):
        """
        Add an Atom to this Residue and store location in index

        :param atom: Atom to add to Residue
        :return: None
        """
        self.atoms.append(atom)
        self.name_to_num[atom.name] = len(self.atoms) - 1


class Frame:
    """
    Hold Atom data separated into Residues
    """
    def __init__(self, gro=None, xtc=None, itp=None, frame_start=0, xtc_reader="simpletraj"):
        """
        Return Frame instance having read Residues and Atoms from GRO if provided

        :param gro: GROMACS GRO file to read initial frame and extract residues
        :param xtc: GROMACS XTC file to read subsequent frames
        :param itp: GROMACS ITP file to read masses and charges
        :return: Frame instance
        """
        self.name = ""
        self.residues = []
        self.number = frame_start - 1
        self.time = 0
        self.numframes = 0
        self.natoms = 0
        self.box = np.zeros(3, dtype=np.float32)

        self._xtc_buffer = None

        if gro is not None:
            from .framereader import get_frame_reader
            self._trajreader = get_frame_reader(gro, traj=xtc, frame_start=frame_start)

            self._trajreader.initialise_frame(self)

            if self._trajreader.num_atoms != self.natoms:
                raise AssertionError("Number of atoms does not match between gro and xtc files.")
            self.numframes += self._trajreader.num_frames

            if itp is not None:
                self._parse_itp(itp)

    @classmethod
    def instance_from_reader(cls, reader):
        """
        Return Frame instance initialised from existing FrameReader object

        :param FrameReader reader: FrameReader object
        :return: Frame instance
        """
        obj = cls()
        obj._trajreader = reader
        obj._trajreader.initialise_frame(obj)
        return obj

    def __len__(self):
        return len(self.residues)

    def __iter__(self):
        return iter(self.residues)

    def __getitem__(self, item):
        return self.residues[item]

    def __repr__(self):
        rep = self.name + "\n"
        atoms = []
        for res in self.residues:
            for atom in res:
                atoms.append(repr(atom))
        rep += "\n".join(atoms)
        return rep

    def yield_resname_in(self, container):
        for res in self:
            if res.name in container:
                yield res

    def next_frame(self):
        """
        Read next frame from input XTC.

        :return: True if successful else False
        """
        result = self._trajreader.read_next(self)
        if result:
            self.number += 1
        return result

    def write_xtc(self, filename):
        """
        Write frame to output XTC file.

        :param filename: XTC filename to write to
        """
        if self._xtc_buffer is None:
            try:
                import mdtraj
            except ImportError as e:
                if "scipy" in repr(e):
                    e.msg = "XTC output with MDTraj also requires Scipy"
                else:
                    e.msg = "XTC output requires the module MDTraj (and probably Scipy)"
                raise

            backup_file(filename)
            self._xtc_buffer = mdtraj.formats.XTCTrajectoryFile(filename, mode="w")

        xyz = np.ndarray((1, self.natoms, 3), dtype=np.float32)
        i = 0
        for residue in self.residues:
            for atom in residue.atoms:
                xyz[0][i] = atom.coords
                i += 1

        time = np.array([self.time], dtype=np.float32)
        step = np.array([self.number], dtype=np.int32)

        box = np.zeros((1, 3, 3), dtype=np.float32)
        for i in range(3):
            box[0][i][i] = self.box[i]

        self._xtc_buffer.write(xyz, time=time, step=step, box=box)

    def _parse_itp(self, filename):
        """
        Parse a GROMACS ITP file to extract atom charges/masses.

        Optional but requires that ITP contains only a single residue.

        :param filename: Filename of GROMACS ITP to read
        """
        with CFG(filename) as itp:
            itpres = Residue(itp["moleculetype"][0][0])
            for line in itp["atoms"]:
                atom = Atom(num=int(line[0]) - 1, type=line[1], name=line[4], charge=float(line[6]), mass=float(line[7]))
                itpres.add_atom(atom)

            for res in self.residues:
                if res.name == itpres.name:
                    for atom, itpatom in zip(res, itpres):
                        atom.add_missing_data(itpatom)

    def output(self, filename, format="gro"):
        """
        Write coordinates from Frame to file.

        :param filename: Name of file to write to
        :param format: Format to write e.g. 'gro', 'lammps'
        """
        outputs = {"gro": self._get_gro_lines,
                   "lammps": self._get_lammps_data_lines}
        try:
            lines = outputs[format]()
            file_write_lines(filename, lines)
        except KeyError:
            print("ERROR: Invalid output format {0}, coordinates will not be output.".format(format))

    def _get_lammps_data_lines(self):
        """
        Return lines of LAMMPS DATA file.

        :return List[str]: Lines of DATA file containing current coordinates

        """
        raise NotImplementedError("LAMMPS Data output has not yet been implemented.")

    def _get_gro_lines(self):
        """
        Return lines of GRO file.

        :return List[str]: Lines of GRO file containing current coordinates
        """
        ret_lines = [
            self.name,
            "{0:5d}".format(self.natoms)
        ]

        i = 1
        format_string = "{0:5d}{1:5s}{2:>5s}{3:5d}{4:8.3f}{5:8.3f}{6:8.3f}"
        for res in self.residues:
            for atom in res:
                ret_lines.append(format_string.format(res.num, res.name, atom.name, i, *atom.coords))
                i += 1

        ret_lines.append("{0:10.5f}{1:10.5f}{2:10.5f}".format(*self.box))

        return ret_lines

    def add_residue(self, residue):
        """
        Add a Residue to this Frame

        :param residue: Residue to add
        """
        self.residues.append(residue)
