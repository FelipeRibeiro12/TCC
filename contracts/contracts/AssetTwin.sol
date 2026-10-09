// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

contract AssetTwin is ERC721, Ownable {
    // o 0 fica reservado como "não existe" para o ShipmentEscrow, então o primeiro id é 1
    uint256 public ultimoTwinId;

    // os metadados ficam fora da cadeia, aqui só o resumo criptográfico
    mapping(uint256 => bytes32) public hashMetadados;

    event TwinCriado(uint256 indexed twinId, address indexed dono, bytes32 hashMetadados);
    event HashMetadadosAtualizado(uint256 indexed twinId, bytes32 hashAnterior, bytes32 hashNovo);

    constructor(address donoInicial)
        ERC721("Claustrum Asset Twin", "CTWIN")
        Ownable(donoInicial)
    {}

    function criarTwin(address para, bytes32 hashInicial) external onlyOwner returns (uint256 twinId) {
        require(para != address(0), "dono invalido");
        require(hashInicial != bytes32(0), "hash vazio");

        twinId = ++ultimoTwinId;
        // grava antes do mint porque _safeMint chama o destinatário se ele for contrato
        hashMetadados[twinId] = hashInicial;
        _safeMint(para, twinId);

        emit TwinCriado(twinId, para, hashInicial);
    }

    // só o dono do contrato, como no mint: o dono do token é o contratante e não deve reescrever o que o serviço já usou como referência
    function atualizarHashMetadados(uint256 twinId, bytes32 hashNovo) external onlyOwner {
        require(existe(twinId), "twin inexistente");
        require(hashNovo != bytes32(0), "hash vazio");

        bytes32 hashAnterior = hashMetadados[twinId];
        hashMetadados[twinId] = hashNovo;

        emit HashMetadadosAtualizado(twinId, hashAnterior, hashNovo);
    }

    function existe(uint256 twinId) public view returns (bool) {
        return _ownerOf(twinId) != address(0);
    }
}
