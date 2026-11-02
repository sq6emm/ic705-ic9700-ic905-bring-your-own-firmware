// Export analyzed functions as decompiler C. Run as a postScript after importing an ELF.
// @category IC705
import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ExportRecovered extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args=getScriptArgs();
        if(args.length!=1)throw new IllegalArgumentException("Output C filename required");
        Path output=Paths.get(args[0]).toAbsolutePath();
        Files.createDirectories(output.getParent());
        DecompInterface decompiler=new DecompInterface();
        if(!decompiler.openProgram(currentProgram))throw new IllegalStateException(decompiler.getLastMessage());
        int good=0,failed=0;
        try(BufferedWriter writer=Files.newBufferedWriter(output,StandardCharsets.UTF_8,StandardOpenOption.CREATE_NEW)) {
            writer.write("/* IC-705 recovered image: "+currentProgram.getName()+". Ghidra decompiler output. */\n");
            FunctionIterator functions=currentProgram.getFunctionManager().getFunctions(true);
            while(functions.hasNext()&&!monitor.isCancelled()) {
                Function function=functions.next();
                if(function.isExternal())continue;
                DecompileResults result=decompiler.decompileFunction(function,60,monitor);
                writer.write("\n/* "+function.getEntryPoint()+" "+function.getName()+" */\n");
                if(result.decompileCompleted()&&result.getDecompiledFunction()!=null) {
                    writer.write(result.getDecompiledFunction().getC());good++;
                } else {
                    writer.write("/* Decompilation incomplete: "+result.getErrorMessage().replace("*/","* /")+" */\n");failed++;
                }
            }
        } finally {decompiler.dispose();}
        println("Exported "+good+" functions; incomplete: "+failed+"; "+output);
    }
}
