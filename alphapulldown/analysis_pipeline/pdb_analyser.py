""" 
    A script that survey all interfaces and obtain all the residues on the interface

    Copyright (c) 2024 European Molecular Biology Laboratory

    Author:Dingquan Yu <dingquan.yu@embl-hamburg.de>
"""
from absl import logging
import subprocess
import os
import tempfile
from typing import Any, List, Dict, Union
import numpy as np
from itertools import combinations, accumulate
import pandas as pd
from Bio.PDB import PDBParser, PDBIO
from biopandas.pdb import PandasPdb
from pyrosetta.io import pose_from_pdb
from pyrosetta.rosetta.core.scoring import get_score_function
import pyrosetta
pyrosetta.init()
logging.set_verbosity(logging.INFO)


class PDBAnalyser:
    """
    A class that store pandas dataframe of all the information 
    of the residues in a PDB file 
    """

    def __init__(self, pdb_file_path: str) -> None:
        self.pdb_file_path = pdb_file_path
        self.pdb_pandas = PandasPdb().read_pdb(pdb_file_path)
        self.pdb = PDBParser().get_structure("ranked_0", pdb_file_path)[0]
        self.pdb_df = self.pdb_pandas.df['ATOM']
        self.chain_combinations = {}
        self.get_all_combinations_of_chains()
        self.calculate_padding_of_chains()
        pass

    def calculate_padding_of_chains(self):
        """
        A method that calculate how many residues need to be padded

        e.g. all residue indexes in the 1st chain do not need to be padded
        all residue indexes in the 2nd chain need to be padded with len(1st chain)
        all residue indexes in the 3nd chain need to be padded with len(1st chain) + len(2nd chain)
        """
        self.chain_cumsum = dict()
        residue_counts = dict()
        for chain in self.pdb:
            residue_counts[chain.id] = sum(1 for _ in chain.get_residues())
        cum_sum = list(accumulate([0] + list(residue_counts.values())[:-1]))
        for chain_id, cs in zip(residue_counts.keys(), cum_sum):
            self.chain_cumsum[chain_id] = cs

    def get_all_combinations_of_chains(self):
        """A method that get all the paiwise combination of chains"""
        unique_chain_ids = pd.unique(self.pdb_df.chain_id)
        if len(unique_chain_ids) > 1:
            chain_combinations = combinations(unique_chain_ids, 2)
            for idx, combo in enumerate(chain_combinations):
                self.chain_combinations.update(
                    {f"interface_{idx+1}": combo}
                )
        else:
            self.chain_combinations = unique_chain_ids

    def retrieve_C_beta_coords(self, chain_df: pd.DataFrame) -> np.ndarray:
        """
        A method that retrieve the x,y,z coords of the C beta atoms of one chain

        Args:
            chain_df : a pandas dataframe that belongs to one chain

        Return:
            a numpy array of all C-beta atoms x,y,z coordinates

        Note:
        Will retrieve C-alpha atom coords if the residue is a glycine
        """
        mask = (chain_df['atom_name'] == 'CB') | (
            (chain_df['residue_name'] == 'GLY') & (chain_df['atom_name'] == 'CA'))
        subdf = chain_df[mask]
        return subdf[['x_coord', 'y_coord', 'z_coord']].values

    def obtain_interface_residues(self, chain_df_1: pd.DataFrame, chain_df_2: pd.DataFrame, cutoff: int = 5) -> Union[None, set]:
        """A method that get all the residues on the interface within the cutoff"""
        chain_1_CB_coords = self.retrieve_C_beta_coords(chain_df_1)
        chain_2_CB_coords = self.retrieve_C_beta_coords(chain_df_2)

        distance_mtx = np.sqrt(
            np.sum((chain_1_CB_coords[:, np.newaxis] - chain_2_CB_coords)**2, axis=-1))
        satisfied_residues_chain_1, satisfied_residues_chain_2 = np.where(
            distance_mtx < cutoff)
        if (len(satisfied_residues_chain_1) > 0) and (len(satisfied_residues_chain_2) > 0):
            return (satisfied_residues_chain_1, satisfied_residues_chain_2)

        else:
            print(f"No interface residues are found.")
            return None

    def calculate_average_pae(self, pae_mtx: np.ndarray, chain_id_1: str, chain_id_2: str,
                              chain_1_residues: np.ndarray, chain_2_residues: np.ndarray) -> float:
        """
        A method to calculate average interface pae 

        Args:
        pae_mtx: An array with PAE values from AlphaFold predictions. shape: num_res * num_res
        chain_1_residues: a row vector. shape: 1 * num_selected_res_chain_1
        chain_2_residues: a row vector. shape: 1 * num_selected_res_chain_2

        Return:
        float: average PAE value of the interface residues
        """
        pae_sum = 0
        chain_1_pad_num = self.chain_cumsum[chain_id_1]
        chain_2_pad_num = self.chain_cumsum[chain_id_2]
        satisfied_residues_chain_1 = [
            i + chain_1_pad_num for i in chain_1_residues]
        satisfied_residues_chain_2 = [
            i + chain_2_pad_num for i in chain_2_residues]
        total_num = len(chain_1_residues)
        for i, j in zip(satisfied_residues_chain_1, satisfied_residues_chain_2):
            pae_sum += pae_mtx[i, j]
            pae_sum += pae_mtx[j, i]

        return pae_sum / (2*total_num)

    def calculate_average_plddt(self, chain_1_plddt: List[float], chain_2_plddt: List[float],
                                chain_1_residues: np.ndarray, chain_2_residues: np.ndarray) -> float:
        """
        A method to calculate average interface plddt 

        Args:
        chain_1_plddt: a list of plddt values of the residues on chain_1
        chain_2_plddt: a list of plddt values of the residues on chain_2 
        chain_1_residues: a row vector. shape: 1 * num_selected_res_chain_1
        chain_2_residues: a row vector. shape: 1 * num_selected_res_chain_2

        Return:
        float: average PAE value of the interface residues
        """
        plddt_sum = 0
        total_num = len(set(chain_1_residues)) + len(set(chain_2_residues))
        for i, j in zip(set(chain_1_residues), set(chain_2_residues)):
            plddt_sum += chain_1_plddt[i]
            plddt_sum += chain_2_plddt[j]

        return plddt_sum / total_num

    def update_df(self, input_df):
        for interface in input_df.interface:
            chain_1, chain_2 = interface.split("_")
            subdf = input_df[input_df['interface'] == interface]
            subdf['interface'] = f"{chain_2}_{chain_1}"
            output_df = pd.concat([input_df, subdf])
        return output_df

    def calculate_binding_energy(self, chain_1_id: str, chain_2_id: str) -> float:
        """Calculate binding energer of 2 chains using pyRosetta"""
        chain_1_structure, chain_2_structure = self.pdb[chain_1_id], self.pdb[chain_2_id]
        with tempfile.NamedTemporaryFile(suffix='.pdb') as temp:
            pdbio = PDBIO()
            pdbio.set_structure(chain_1_structure)
            pdbio.set_structure(chain_2_structure)
            pdbio.save(temp.name)
            complex_pose = pose_from_pdb(temp.name)

        with tempfile.NamedTemporaryFile(suffix='.pdb') as temp1, tempfile.NamedTemporaryFile(suffix='.pdb') as temp2:
            pdbio = PDBIO()
            pdbio.set_structure(chain_1_structure)
            pdbio.save(file=temp1.name)
            chain_1_pose = pose_from_pdb(temp1.name)
            pdbio = PDBIO()
            pdbio.set_structure(chain_2_structure)
            pdbio.save(file=temp2.name)
            chain_2_pose = pose_from_pdb(temp2.name)

        sfxn = get_score_function(True)
        complex_energy = sfxn(complex_pose)
        chain_1_energy, chain_2_energy = sfxn(chain_1_pose), sfxn(chain_2_pose)
        return complex_energy - chain_1_energy - chain_2_energy

    def run_and_summarise_pi_score(self, work_dir, pdb_path: str,
                                   surface_thres: int = 2, interface_name: str = "") -> pd.DataFrame:
        """A function to calculate all predicted models' pi_scores and make a pandas df of the results"""

        try:
            subprocess.run(
                f"source activate pi_score && export PYTHONPATH=/software:$PYTHONPATH && python /software/pi_score/run_piscore_wc.py -p {pdb_path} -o {work_dir} -s {surface_thres} -ps 10", shell=True, executable='/bin/bash')
            csv_files = [f for f in os.listdir(
                work_dir) if 'filter_intf_features' in f]
            pi_score_files = [f for f in os.listdir(
                work_dir) if 'pi_score_' in f]
            filtered_df = pd.read_csv(os.path.join(work_dir, csv_files[0]))
        except:
            logging.warning(
                f"PI score calculation has failed. Will proceed with the rest of the jobs")
            filtered_df = dict()
            for k in "pdb,chains,Num_intf_residues,Polar,Hydrophobhic,Charged,contact_pairs,contact_pairs, sc, hb, sb, int_solv_en, int_area, pvalue,pi_score".split(","):
                filtered_df.update({k: ["None"]})
            filtered_df = pd.DataFrame.from_dict({
                filtered_df
            })

        if filtered_df.shape[0] == 0:
            for column in filtered_df.columns:
                filtered_df[column] = ["None"]
            filtered_df['pi_score'] = "No interface detected"
            filtered_df['interface'] = interface_name
            filtered_df = self.update_df(filtered_df)
        else:

            filtered_df = self.update_df(filtered_df)
            with open(os.path.join(work_dir, pi_score_files[0]), 'r') as f:
                lines = [l for l in f.readlines() if "#" not in l]
                if len(lines) > 0:
                    pi_score = pd.read_csv(
                        os.path.join(work_dir, pi_score_files[0]))
                else:
                    pi_score = pd.DataFrame.from_dict(
                        {"pi_score": ['SC:  mds: too many atoms']})
                f.close()
            pi_score['interface'] = pi_score['chains']
            filtered_df = pd.merge(filtered_df, pi_score, on=['interface'])
            try:
                filtered_df = filtered_df.drop(
                    columns=["#PDB", "pdb", " pvalue", "chains", "predicted_class"])
            except:
                pass

        return filtered_df

    def calculate_pi_score(self, pi_score_output_dir: str, interface: int = "") -> pd.DataFrame:
        """Run the PI-score pipeline between the 2 chains"""
        pi_score_df = self.run_and_summarise_pi_score(
                pi_score_output_dir, self.pdb_file_path, interface_name=interface)
        return pi_score_df

    def __call__(self, pi_score_output_dir: str, pae_mtx: np.ndarray, plddt: Dict[str, List[float]], cutoff: float = 12) -> Any:
        """
        Obtain interface residues and calculate average PAE, average plDDT of the interface residues

        Args:
        pae_mtx: An array with PAE values from AlphaFold predictions. shape: num_res * num_res
        plddt: A dictionary that contains plddt score of each chain. 
        cutoff: cutoff value when determining whether 2 residues are interacting. deemed to be interacting 
        if the distance between two C-Beta is smaller than cutoff

        """
        output_df = pd.DataFrame()
        if type(self.chain_combinations) != dict:
            print(
                f"Your PDB structure seems to be a monomeric structure. The programme will stop.")
            import sys
            sys.exit()
        else:
            for k, v in self.chain_combinations.items():
                chain_1_id, chain_2_id = v
                chain_1_df, chain_2_df = self.pdb_df[self.pdb_df['chain_id'] ==
                                                     chain_1_id], self.pdb_df[self.pdb_df['chain_id'] == chain_2_id]
                pi_score_df = self.calculate_pi_score(pi_score_output_dir,
                                                      interface=f"{chain_1_id}_{chain_2_id}")
                pi_score_df = self.update_df(pi_score_df)
                chain_1_plddt, chain_2_plddt = plddt[chain_1_id], plddt[chain_2_id]
                interface_residues = self.obtain_interface_residues(
                    chain_1_df, chain_2_df, cutoff=cutoff)
                if interface_residues is not None:
                    average_interface_pae = self.calculate_average_pae(pae_mtx, chain_1_id, chain_2_id,
                                                                       interface_residues[0], interface_residues[1])
                    binding_energy = self.calculate_binding_energy(
                        chain_1_id, chain_2_id)
                    average_interface_plddt = self.calculate_average_plddt(chain_1_plddt, chain_2_plddt,
                                                                           interface_residues[0], interface_residues[1])
                else:
                    average_interface_pae = "No interface residues detected"
                    average_interface_plddt = "No interface residues detected"
                    binding_energy = "None"
                other_measurements_df = pd.DataFrame.from_dict({
                    "average_interface_pae": [average_interface_pae],
                    "average_interface_plddt": [average_interface_plddt],
                    "binding_energy": [binding_energy],
                    "interface": [f"{chain_1_id}_{chain_2_id}"]
                })
                output_df = pd.concat([output_df, other_measurements_df])

        return pd.merge(output_df, pi_score_df, how='left', on='interface')
